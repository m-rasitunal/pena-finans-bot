import os
import logging
import json
import csv
import io
from datetime import date, datetime, timedelta
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import httpx

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")
ADMIN_ID = 6230496507

logging.basicConfig(level=logging.WARNING)

SYSTEM_PROMPT = """Sen Pena Medya şirketinin finansal asistanısın. Türkçe sohbet dilinde yazılan mesajları JSON formatında ayrıştırırsın.

Şirket: Pena Medya
Bankalar: Ziraat, Halk, Vakıf, Kuveyt
Ortaklar: Raşit, Ömer
KDV: %20 (faturalar KDV hariç girilir)

SADECE JSON döndür, başka hiçbir şey yazma:

{
  "islem": "gelir|gider|fatura_kes|fatura_odendi|borc_odendi|avans|iade|transfer|bakiye_gir|sozlesme|ozet|anlik_durum|kasa|alacaklar|borclar|ortaklar|proje|detay|bilinmiyor",
  "tutar": 45000,
  "aciklama": "açıklama",
  "musteri": "müşteri adı",
  "tedarikci": "tedarikçi adı",
  "proje": "proje adı",
  "sozlesme_id": null,
  "kategori": "kategori",
  "banka": "Ziraat",
  "hedef_banka": "Halk",
  "kisi": "Raşit",
  "vade_tarihi": "2026-07-15",
  "tarih": "2026-06-01",
  "tarih_baslangic": "2026-06-01",
  "tarih_bitis": "2026-06-30",
  "sozlesme_tutari": null,
  "sozlesme_bitis": null,
  "fatura_id": null,
  "onay_mesaji": "onay metni"
}

İşlem tespiti:
- "TRT'den para geldi / ödeme aldık" → gelir (banka yoksa null bırak)
- "gider ödedik / ödeme yaptık" → gider
- "fatura kestim / fatura kesildi" → fatura_kes (alacak oluşturur, kasa hareketi DEĞİL)
- "TRT ödedi / tahsilat yapıldı" → fatura_odendi (alacak kapanır + kasa hareketi)
- "borç ödedik" → borc_odendi
- "avans verdik" → avans
- "iade aldı / iade etti" → iade
- "Ziraat'tan Halk'a transfer" → transfer
- "Ziraat bakiyesi 45000 / başlangıç bakiyesi" → bakiye_gir
- "Gebze ile sözleşme imzaladık" → sozlesme
- "özet ver / bu ay özet" → ozet (tarih aralığı soracak)
- "anlık durum / durum nedir" → anlik_durum
- "kasada ne var / banka bakiyeleri" → kasa
- "alacaklarımız / kimden alacağımız" → alacaklar
- "borçlarımız / kime borcumuz" → borclar
- "ortak bakiye / Raşit ne durumda" → ortaklar
- "Gebze projesi / proje durumu" → proje
- "TRT detayı / geçmiş hareketler" → detay

Tarihler için: bugün 2026-06-01. "dün" = 2026-05-31, "geçen ay" = Mayıs 2026.
Tutar her zaman sayı (45000 gibi, "45 bin" değil).
"""

# ============ SUPABASE HELPERS ============

async def sb_get(table, query=""):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/{table}{query}",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})
        return r.json()

async def sb_post(table, data):
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{SUPABASE_URL}/rest/v1/{table}",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
                     "Content-Type": "application/json", "Prefer": "return=representation"},
            json=data)
        return r.json()

async def sb_patch(table, query, data):
    async with httpx.AsyncClient() as c:
        r = await c.patch(f"{SUPABASE_URL}/rest/v1/{table}{query}",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
                     "Content-Type": "application/json", "Prefer": "return=representation"},
            json=data)
        return r.json()

async def sb_delete(table, query):
    async with httpx.AsyncClient() as c:
        r = await c.delete(f"{SUPABASE_URL}/rest/v1/{table}{query}",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})
        return r.status_code

async def banka_id_bul(banka_adi):
    if not banka_adi:
        return None, None
    data = await sb_get("banka_hesaplari", f"?banka=ilike.*{banka_adi}*&aktif=eq.true")
    if data and isinstance(data, list) and len(data) > 0:
        return data[0]["id"], data[0]["ad"]
    return None, None

async def claude_analiz(mesaj):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 600, "system": SYSTEM_PROMPT,
                  "messages": [{"role": "user", "content": mesaj}]})
        text = r.json()["content"][0]["text"].strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"): text = text[4:]
        return json.loads(text.strip())

# ============ İŞLEM FONKSİYONLARI ============

async def islem_yap(parsed, update):
    islem = parsed.get("islem")
    tutar = parsed.get("tutar")
    bugun = parsed.get("tarih") or date.today().isoformat()

    # --- GELİR ---
    if islem == "gelir":
        banka_id, banka_ad = await banka_id_bul(parsed.get("banka"))
        await sb_post("hesap_hareketleri", {
            "tarih": bugun, "tur": "gelir", "tutar": tutar,
            "aciklama": parsed.get("aciklama", ""),
            "karsi_taraf": parsed.get("musteri", ""),
            "proje_adi": parsed.get("proje", ""),
            "banka_hesabi_id": banka_id,
            "kategori": parsed.get("kategori", "gelir")
        })
        return f"Kaydedildi!\n\n{tutar:,.0f} TL gelir\nBanka: {banka_ad or '?'}\n{parsed.get('aciklama','')}"

    # --- GİDER ---
    elif islem == "gider":
        banka_id, banka_ad = await banka_id_bul(parsed.get("banka"))
        await sb_post("hesap_hareketleri", {
            "tarih": bugun, "tur": "gider", "tutar": tutar,
            "aciklama": parsed.get("aciklama", ""),
            "karsi_taraf": parsed.get("tedarikci", ""),
            "proje_adi": parsed.get("proje", ""),
            "banka_hesabi_id": banka_id,
            "kategori": parsed.get("kategori", "gider")
        })
        return f"Kaydedildi!\n\n{tutar:,.0f} TL gider\nBanka: {banka_ad or '?'}\n{parsed.get('aciklama','')}"

    # --- FATURA KES (alacak oluştur) ---
    elif islem == "fatura_kes":
        kdv = round(tutar * 0.20, 2)
        toplam = round(tutar * 1.20, 2)
        result = await sb_post("kesilen_faturalar", {
            "fatura_no": "", "musteri_adi": parsed.get("musteri", ""),
            "proje_adi": parsed.get("proje", ""),
            "kdv_haric_tutar": tutar,
            "fatura_tarihi": bugun,
            "vade_tarihi": parsed.get("vade_tarihi"),
            "sozlesme_id": parsed.get("sozlesme_id"),
            "durum": "bekliyor"
        })
        fno = result[0].get("fatura_no", "PENA-????") if isinstance(result, list) and result else "PENA-????"
        return (f"Fatura Kesildi! (Alacak oluştu)\n\n"
                f"No: {fno}\nMusteri: {parsed.get('musteri','')}\n"
                f"KDV haric: {tutar:,.0f} TL\nToplam: {toplam:,.0f} TL\n"
                f"Vade: {parsed.get('vade_tarihi') or 'Belirtilmedi'}\n\n"
                f"NOT: Bu bir alacaktir. Para geldiginde 'odeme aldik' yaz.")

    # --- FATURA ÖDENDİ (alacak kapat + kasa) ---
    elif islem == "fatura_odendi":
        banka_id, banka_ad = await banka_id_bul(parsed.get("banka"))
        musteri = parsed.get("musteri", "")
        # Açık faturayı bul
        faturalar = await sb_get("kesilen_faturalar", f"?musteri_adi=ilike.*{musteri}*&durum=in.(bekliyor,kismi)&order=fatura_tarihi.desc")
        if not faturalar or not isinstance(faturalar, list):
            return f"{musteri} adına açık fatura bulunamadı."
        
        fatura = faturalar[0]
        fatura_id = fatura["id"]
        fatura_toplam = float(fatura.get("toplam_tutar") or 0)
        
        # Kısmi ödeme mi tam ödeme mi?
        yeni_durum = "odendi" if tutar >= fatura_toplam else "kismi"
        await sb_patch("kesilen_faturalar", f"?id=eq.{fatura_id}", {"durum": yeni_durum, "odeme_tarihi": bugun})
        
        # Kasa hareketi oluştur
        await sb_post("hesap_hareketleri", {
            "tarih": bugun, "tur": "gelir", "tutar": tutar,
            "aciklama": f"{musteri} fatura odemesi",
            "karsi_taraf": musteri, "banka_hesabi_id": banka_id,
            "kesilen_fatura_id": fatura_id,
            "kategori": "fatura tahsilati"
        })
        return (f"Tahsilat kaydedildi!\n\n"
                f"Musteri: {musteri}\n"
                f"Tutar: {tutar:,.0f} TL\n"
                f"Banka: {banka_ad or '?'}\n"
                f"Fatura: {fatura.get('fatura_no','?')} - {yeni_durum}")

    # --- BORÇ ÖDENDİ ---
    elif islem == "borc_odendi":
        banka_id, banka_ad = await banka_id_bul(parsed.get("banka"))
        await sb_post("hesap_hareketleri", {
            "tarih": bugun, "tur": "gider", "tutar": tutar,
            "aciklama": parsed.get("aciklama", "Borc odemesi"),
            "karsi_taraf": parsed.get("tedarikci", ""),
            "banka_hesabi_id": banka_id, "kategori": "borc odemesi"
        })
        # Gelen fatura varsa güncelle
        tedarikci = parsed.get("tedarikci", "")
        if tedarikci:
            faturalar = await sb_get("gelen_faturalar", f"?tedarikci_adi=ilike.*{tedarikci}*&durum=in.(bekliyor,kismi)")
            if faturalar and isinstance(faturalar, list):
                await sb_patch("gelen_faturalar", f"?id=eq.{faturalar[0]['id']}", {"durum": "odendi", "odeme_tarihi": bugun})
        return f"Borc odemesi kaydedildi!\n\n{tutar:,.0f} TL\nBanka: {banka_ad or '?'}\n{parsed.get('aciklama','')}"

    # --- AVANS ---
    elif islem == "avans":
        kisi = parsed.get("kisi", "")
        await sb_post("ic_hareketler", {
            "kisi": kisi, "tur": "avans", "tutar": tutar,
            "tarih": bugun, "aciklama": parsed.get("aciklama", ""), "durum": "acik"
        })
        return f"{kisi}'e {tutar:,.0f} TL avans kaydedildi."

    # --- İADE ---
    elif islem == "iade":
        kisi = parsed.get("kisi", "")
        await sb_post("ic_hareketler", {
            "kisi": kisi, "tur": "iade", "tutar": tutar,
            "tarih": bugun, "aciklama": parsed.get("aciklama", ""), "durum": "kapandi"
        })
        return f"{kisi} {tutar:,.0f} TL iade kaydedildi."

    # --- TRANSFER ---
    elif islem == "transfer":
        kaynak_id, kaynak_ad = await banka_id_bul(parsed.get("banka"))
        hedef_id, hedef_ad = await banka_id_bul(parsed.get("hedef_banka"))
        await sb_post("hesap_hareketleri", {
            "tarih": bugun, "tur": "transfer", "tutar": tutar,
            "aciklama": f"{kaynak_ad} → {hedef_ad} transfer",
            "banka_hesabi_id": kaynak_id,
            "transfer_hedef_banka_id": hedef_id,
            "kategori": "transfer"
        })
        return f"Transfer kaydedildi!\n\n{kaynak_ad} → {hedef_ad}\n{tutar:,.0f} TL"

    # --- BAKİYE GİR ---
    elif islem == "bakiye_gir":
        banka_id, banka_ad = await banka_id_bul(parsed.get("banka"))
        if not banka_id:
            return "Banka bulunamadi. Ziraat, Halk, Vakif veya Kuveyt yaz."
        await sb_post("banka_bakiye_giris", {
            "banka_hesabi_id": banka_id, "tutar": tutar,
            "tarih": bugun, "aciklama": "Baslangic bakiyesi"
        })
        return f"{banka_ad} baslangic bakiyesi: {tutar:,.0f} TL kaydedildi."

    # --- SÖZLEŞME ---
    elif islem == "sozlesme":
        result = await sb_post("sozlesmeler", {
            "musteri_adi": parsed.get("musteri", ""),
            "proje_adi": parsed.get("proje", ""),
            "toplam_tutar": parsed.get("sozlesme_tutari") or tutar,
            "baslangic_tarihi": bugun,
            "bitis_tarihi": parsed.get("sozlesme_bitis"),
            "aciklama": parsed.get("aciklama", ""),
            "durum": "aktif"
        })
        s = result[0] if isinstance(result, list) and result else {}
        return (f"Sozlesme kaydedildi!\n\n"
                f"Musteri: {parsed.get('musteri','')}\n"
                f"Proje: {parsed.get('proje','')}\n"
                f"Toplam: {(parsed.get('sozlesme_tutari') or tutar or 0):,.0f} TL\n"
                f"Bitis: {parsed.get('sozlesme_bitis') or 'Belirtilmedi'}")

    # --- ANLIK DURUM (tam dashboard) ---
    elif islem == "anlik_durum":
        return await anlik_durum_getir()

    # --- KASA ---
    elif islem == "kasa":
        return await kasa_getir()

    # --- ALACAKLAR ---
    elif islem == "alacaklar":
        return await alacaklar_getir()

    # --- BORÇLAR ---
    elif islem == "borclar":
        return await borclar_getir()

    # --- ORTAKLAR ---
    elif islem == "ortaklar":
        return await ortaklar_getir()

    # --- PROJE ---
    elif islem == "proje":
        return await proje_getir(parsed.get("proje") or parsed.get("musteri", ""))

    # --- DETAY ---
    elif islem == "detay":
        return await detay_getir(parsed)

    else:
        return parsed.get("onay_mesaji", "Anlayamadim. Ornek: 'TRTden 45 bin geldi Ziraat'a'")

# ============ SORGULAR ============

async def anlik_durum_getir():
    bankalar = await sb_get("banka_bakiyeleri")
    alacaklar = await sb_get("bekleyen_alacaklar")
    borclar = await sb_get("bekleyen_borclar")
    ic = await sb_get("ic_bakiye")

    # Kasa
    toplam_kasa = 0
    kasa_text = "KASA\n"
    if isinstance(bankalar, list):
        for b in bankalar:
            bak = float(b.get("bakiye") or 0)
            toplam_kasa += bak
            kasa_text += f"{b['banka']}: {bak:,.0f} TL\n"
    kasa_text += f"Toplam: {toplam_kasa:,.0f} TL"

    # Alacaklar
    toplam_alacak = sum(float(r.get("toplam_tutar") or 0) for r in alacaklar) if isinstance(alacaklar, list) else 0
    geciken_alacak = sum(float(r.get("toplam_tutar") or 0) for r in alacaklar if isinstance(alacaklar, list) and (r.get("gecikme_gunu") or 0) > 0)

    # Borçlar
    toplam_borc = sum(float(r.get("toplam_tutar") or 0) for r in borclar) if isinstance(borclar, list) else 0

    # Net
    net = toplam_kasa + toplam_alacak - toplam_borc

    # Ortaklar
    ortak_text = "ORTAKLAR\n"
    if isinstance(ic, list):
        for r in ic:
            nb = float(r.get("net_borc") or 0)
            ortak_text += f"{r['kisi']}: {nb:,.0f} TL borçlu\n" if nb > 0 else f"{r['kisi']}: Temiz\n"

    return (f"ANLIK DURUM\n\n"
            f"{kasa_text}\n\n"
            f"ALACAKLAR\n"
            f"Toplam: {toplam_alacak:,.0f} TL\n"
            f"Gecikmiş: {geciken_alacak:,.0f} TL\n\n"
            f"BORCLAR\n"
            f"Toplam: {toplam_borc:,.0f} TL\n\n"
            f"NET FİNANSAL DURUM: {net:,.0f} TL\n\n"
            f"{ortak_text}")

async def kasa_getir():
    bankalar = await sb_get("banka_bakiyeleri")
    if not isinstance(bankalar, list):
        return "Kasa bilgisi alinamadi."
    toplam = 0
    metin = "KASA BAKİYELERİ\n\n"
    for b in bankalar:
        bak = float(b.get("bakiye") or 0)
        toplam += bak
        metin += f"{b['banka']}: {bak:,.0f} TL\n"
    metin += f"\nToplam: {toplam:,.0f} TL"
    return metin

async def alacaklar_getir():
    data = await sb_get("bekleyen_alacaklar")
    if not isinstance(data, list) or not data:
        return "Bekleyen alacak yok!"
    toplam = sum(float(r.get("toplam_tutar") or 0) for r in data)
    metin = f"BEKLEYEN ALACAKLAR ({len(data)} fatura)\n\n"
    for r in data:
        t = float(r.get("toplam_tutar") or 0)
        g = r.get("gecikme_gunu") or 0
        gecikme = f" ⚠ {g} gun gecikti" if g and g > 0 else ""
        vade = r.get("vade_tarihi") or "Vade yok"
        metin += f"{r.get('fatura_no','?')} - {r.get('musteri_adi','?')}\n"
        metin += f"  {r.get('proje_adi','')} | {t:,.0f} TL | Vade: {vade}{gecikme}\n\n"
    metin += f"Toplam: {toplam:,.0f} TL"
    return metin

async def borclar_getir():
    data = await sb_get("bekleyen_borclar")
    if not isinstance(data, list) or not data:
        return "Bekleyen borc yok!"
    toplam = sum(float(r.get("toplam_tutar") or 0) for r in data)
    metin = f"BEKLEYEN BORCLAR ({len(data)} fatura)\n\n"
    for r in data:
        t = float(r.get("toplam_tutar") or 0)
        g = r.get("gecikme_gunu") or 0
        gecikme = f" ⚠ {g} gun gecikti" if g and g > 0 else ""
        metin += f"{r.get('tedarikci_adi','?')} | {r.get('kategori','')} | {t:,.0f} TL{gecikme}\n"
    metin += f"\nToplam: {toplam:,.0f} TL"
    return metin

async def ortaklar_getir():
    data = await sb_get("ic_bakiye")
    detay = await sb_get("ic_hareketler", "?order=tarih.desc&limit=20")
    if not isinstance(data, list):
        return "Ic hareket kaydi yok."
    metin = "ORTAK BAKİYELERİ\n\n"
    for r in data:
        nb = float(r.get("net_borc") or 0)
        tb = float(r.get("toplam_borc") or 0)
        ti = float(r.get("toplam_iade") or 0)
        metin += f"{r['kisi']}\n"
        metin += f"  Toplam avans/borc: {tb:,.0f} TL\n"
        metin += f"  Toplam iade: {ti:,.0f} TL\n"
        metin += f"  Net: {nb:,.0f} TL\n\n"
    if isinstance(detay, list) and detay:
        metin += "SON HAREKETLER\n"
        for r in detay[:10]:
            metin += f"{r.get('tarih','?')} | {r.get('kisi','?')} | {r.get('tur','?')} | {float(r.get('tutar',0)):,.0f} TL\n"
    return metin

async def proje_getir(proje_adi):
    if not proje_adi:
        sozlesmeler = await sb_get("proje_ozet")
        if not isinstance(sozlesmeler, list) or not sozlesmeler:
            return "Kayitli sozlesme/proje yok."
        metin = "PROJELER\n\n"
        for s in sozlesmeler:
            metin += f"{s.get('musteri_adi','?')} - {s.get('proje_adi','?')}\n"
            metin += f"  Sozlesme: {float(s.get('sozlesme_tutari',0)):,.0f} TL\n"
            metin += f"  Faturalandirilan: {float(s.get('faturalandirilan',0)):,.0f} TL\n"
            metin += f"  Kalan: {float(s.get('kalan_faturalanacak',0)):,.0f} TL\n\n"
        return metin
    else:
        data = await sb_get("proje_ozet", f"?proje_adi=ilike.*{proje_adi}*")
        if not isinstance(data, list) or not data:
            data = await sb_get("proje_ozet", f"?musteri_adi=ilike.*{proje_adi}*")
        if not isinstance(data, list) or not data:
            return f"{proje_adi} projesi bulunamadi."
        s = data[0]
        faturalar = await sb_get("kesilen_faturalar", f"?sozlesme_id=eq.{s['sozlesme_id']}&order=fatura_tarihi.desc")
        metin = (f"PROJE: {s.get('proje_adi','?')}\n"
                 f"Musteri: {s.get('musteri_adi','?')}\n"
                 f"Sozlesme: {float(s.get('sozlesme_tutari',0)):,.0f} TL\n"
                 f"Faturalandirilan: {float(s.get('faturalandirilan',0)):,.0f} TL\n"
                 f"Tahsil edilen: {float(s.get('tahsil_edilen',0)):,.0f} TL\n"
                 f"Kalan faturalanacak: {float(s.get('kalan_faturalanacak',0)):,.0f} TL\n\n")
        if isinstance(faturalar, list) and faturalar:
            metin += "FATURALAR\n"
            for f in faturalar:
                metin += f"{f.get('fatura_no','?')} | {float(f.get('toplam_tutar',0)):,.0f} TL | {f.get('durum','?')} | Vade: {f.get('vade_tarihi','?')}\n"
        return metin

async def detay_getir(parsed):
    musteri = parsed.get("musteri", "")
    proje = parsed.get("proje", "")
    t_bas = parsed.get("tarih_baslangic")
    t_bit = parsed.get("tarih_bitis")
    query = "?order=tarih.desc&limit=30"
    if musteri:
        query += f"&karsi_taraf=ilike.*{musteri}*"
    if t_bas:
        query += f"&tarih=gte.{t_bas}"
    if t_bit:
        query += f"&tarih=lte.{t_bit}"
    data = await sb_get("hesap_hareketleri", query)
    if not isinstance(data, list) or not data:
        return "Hareket bulunamadi."
    metin = f"HAREKETLER ({len(data)} kayit)\n\n"
    for r in data:
        t = float(r.get("tutar") or 0)
        tur = "+" if r.get("tur") == "gelir" else "-"
        metin += f"{r.get('tarih','?')} | {tur}{t:,.0f} TL | {r.get('aciklama','')}\n"
    return metin

async def ozet_getir(t_bas, t_bit):
    query = f"?tarih=gte.{t_bas}&tarih=lte.{t_bit}"
    data = await sb_get("hesap_hareketleri", query)
    if not isinstance(data, list) or not data:
        return f"{t_bas} / {t_bit} arasinda kayit yok."
    gelir = sum(float(r.get("tutar",0)) for r in data if r.get("tur") == "gelir")
    gider = sum(float(r.get("tutar",0)) for r in data if r.get("tur") == "gider")
    net = gelir - gider
    return (f"OZET: {t_bas} - {t_bit}\n\n"
            f"Gelir: {gelir:,.0f} TL\n"
            f"Gider: {gider:,.0f} TL\n"
            f"Net: {net:,.0f} TL")

async def excel_export(update):
    data = await sb_get("hesap_hareketleri", "?order=tarih.desc")
    if not isinstance(data, list) or not data:
        await update.message.reply_text("Dışa aktarılacak veri yok.")
        return
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Tarih", "Tur", "Tutar", "Aciklama", "Karsi Taraf", "Proje", "Kategori"])
    for r in data:
        writer.writerow([r.get("tarih",""), r.get("tur",""), r.get("tutar",""),
                         r.get("aciklama",""), r.get("karsi_taraf",""),
                         r.get("proje_adi",""), r.get("kategori","")])
    output.seek(0)
    bugun = date.today().strftime("%Y%m%d")
    await update.message.reply_document(
        document=InputFile(io.BytesIO(output.getvalue().encode("utf-8-sig")),
                           filename=f"pena_hareketler_{bugun}.csv"),
        caption=f"Pena Medya Kasa Hareketleri - {bugun}"
    )

# ============ DURUM YÖNETİMİ ============

bekleyen = {}
sifirlama_bekleyen = set()
ozet_bekleyen = {}
vade_bekleyen = {}
banka_bekleyen = {}

async def sifirla_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Bu komutu kullanma yetkin yok.")
        return
    sifirlama_bekleyen.add(update.effective_user.id)
    await update.message.reply_text(
        "Tum veriler silinecek. Bu islem geri alinamaz.\n\n"
        "Devam etmek icin: EVET SIFIRLA\n"
        "Iptal icin: hayir"
    )

async def excel_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await excel_export(update)

async def mesaj_isle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    mesaj = update.message.text.strip()

    # Sıfırlama onayı
    if uid in sifirlama_bekleyen:
        if mesaj.upper() == "EVET SIFIRLA":
            sifirlama_bekleyen.discard(uid)
            await update.message.reply_text("Siliniyor...")
            try:
                for tablo in ["ic_hareketler", "hesap_hareketleri", "kesilen_faturalar", "gelen_faturalar", "borc_kredi", "banka_bakiye_giris", "sozlesmeler"]:
                    await sb_delete(tablo, "?id=gte.0")
                async with httpx.AsyncClient() as c:
                    await c.post(f"{SUPABASE_URL}/rest/v1/rpc/sifirla_sequence",
                        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}, json={})
                await update.message.reply_text("Tum veriler silindi.")
            except Exception as e:
                await update.message.reply_text(f"Hata: {str(e)}")
        else:
            sifirlama_bekleyen.discard(uid)
            await update.message.reply_text("Iptal edildi.")
        return

    # Vade tarihi bekleniyor
    if uid in vade_bekleyen:
        parsed = vade_bekleyen.pop(uid)
        if mesaj.lower() in ["vade yok", "atla", "gecebilir", "-"]:
            parsed["vade_tarihi"] = None
        else:
            try:
                analiz = await claude_analiz(f"Bu bir tarih: {mesaj}")
                parsed["vade_tarihi"] = analiz.get("vade_tarihi") or analiz.get("tarih")
            except:
                parsed["vade_tarihi"] = None
        bekleyen[uid] = parsed
        vade_str = parsed.get("vade_tarihi") or "Belirtilmedi"
        await update.message.reply_text(
            f"{parsed.get('musteri','')} - {parsed.get('tutar',0):,.0f} TL fatura\nVade: {vade_str}\n\nKaydedeyim mi? (Evet/Hayir)"
        )
        return

    # Banka bekleniyor
    if uid in banka_bekleyen:
        parsed = banka_bekleyen.pop(uid)
        parsed["banka"] = mesaj.strip()
        bekleyen[uid] = parsed
        banka_id, banka_ad = await banka_id_bul(mesaj.strip())
        await update.message.reply_text(
            f"Banka: {banka_ad or mesaj}\nKaydedeyim mi? (Evet/Hayir)"
        )
        return

    # Özet tarih aralığı bekleniyor
    if uid in ozet_bekleyen:
        ozet_bekleyen.pop(uid)
        try:
            parsed2 = await claude_analiz(mesaj)
            t_bas = parsed2.get("tarih_baslangic") or parsed2.get("tarih") or date.today().replace(day=1).isoformat()
            t_bit = parsed2.get("tarih_bitis") or date.today().isoformat()
            sonuc = await ozet_getir(t_bas, t_bit)
            await update.message.reply_text(sonuc)
        except:
            await update.message.reply_text("Tarih anlayamadim. Ornek: 'Haziran 2026' veya '1-30 Haziran'")
        return

    # İşlem onayı bekleniyor
    if uid in bekleyen:
        parsed = bekleyen.pop(uid)
        if mesaj.lower() in ["evet", "e", "yes", "tamam", "ok", "kaydet"]:
            await update.message.reply_text("Kaydediliyor...")
            try:
                sonuc = await islem_yap(parsed, update)
                await update.message.reply_text(sonuc)
            except Exception as e:
                await update.message.reply_text(f"Hata: {str(e)}")
        else:
            await update.message.reply_text("Iptal edildi.")
        return

    await update.message.reply_text("Anliyorum...")

    try:
        parsed = await claude_analiz(mesaj)
    except Exception as e:
        await update.message.reply_text(f"Analiz hatasi: {str(e)}")
        return

    islem = parsed.get("islem", "bilinmiyor")

    # Eksik bilgi kontrolleri
    if islem in ["gelir", "fatura_odendi"] and not parsed.get("banka"):
        parsed["onay_mesaji"] = f"{parsed.get('tutar',0):,.0f} TL hangi bankaya geldi? (Ziraat, Halk, Vakif, Kuveyt)"
        bekleyen[uid] = {**parsed, "_bekleyen_bilgi": "banka"}
        await update.message.reply_text(parsed["onay_mesaji"])
        return

    if islem == "fatura_kes" and not parsed.get("vade_tarihi"):
        vade_bekleyen[uid] = parsed
        await update.message.reply_text(
            f"{parsed.get('musteri','')} icin {parsed.get('tutar',0):,.0f} TL fatura kesilecek.\n\n"
            f"Vade tarihi nedir? (Ornek: 15 Temmuz veya 2026-07-15)\n"
            f"Atlamak icin: 'vade yok' yaz"
        )
        return

    if islem == "gider" and not parsed.get("banka"):
        bekleyen[uid] = parsed
        await update.message.reply_text(
            f"{parsed.get('tutar',0):,.0f} TL hangi bankadan odendi? (Ziraat, Halk, Vakif, Kuveyt)"
        )
        return

    # Özet → tarih sor
    if islem == "ozet":
        ozet_bekleyen[uid] = True
        await update.message.reply_text("Hangi tarih araligi? (Ornek: Haziran 2026, bu ay, 1-15 Haziran)")
        return

    # Direkt sorgular
    if islem in ["anlik_durum", "kasa", "alacaklar", "borclar", "ortaklar", "proje", "detay"]:
        try:
            sonuc = await islem_yap(parsed, update)
            await update.message.reply_text(sonuc)
        except Exception as e:
            await update.message.reply_text(f"Hata: {str(e)}")
        return

    # Kayıt işlemleri → onay iste
    if islem != "bilinmiyor":
        onay = parsed.get("onay_mesaji", "Bu islemi kaydedeyim mi?")
        bekleyen[uid] = parsed
        await update.message.reply_text(f"{onay}\n\nEvet veya Hayir yaz.")
    else:
        await update.message.reply_text(
            parsed.get("onay_mesaji", "Anlayamadim.\n\nOrnekler:\n- TRTden 45 bin geldi Ziraat'a\n- Gebze'ye 38 bin fatura kestim\n- Anlik durum\n- Kasa bakiyeleri")
        )

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("sifirla", sifirla_komutu))
    app.add_handler(CommandHandler("excel", excel_komutu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mesaj_isle))
    print("Pena Finans Botu v2 basladi...")
    app.run_polling(stop_signals=None)

if __name__ == "__main__":
    main()

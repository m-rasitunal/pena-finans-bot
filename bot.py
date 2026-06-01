import os
import logging
import json
import csv
import io
from datetime import date, datetime
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import httpx

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")
ADMIN_ID = 6230496507

logging.basicConfig(level=logging.WARNING)

def para_formatla(tutar):
    """Türk para formatı: 110.000,50 TL"""
    try:
        t = float(tutar)
        if t == int(t):
            return f"{int(t):,}".replace(",", ".") + " TL"
        else:
            tam = int(t)
            kurus = round((t - tam) * 100)
            return f"{tam:,}".replace(",", ".") + f",{kurus:02d} TL"
    except:
        return f"{tutar} TL"

SYSTEM_PROMPT = """Sen Pena Medya şirketinin finansal asistanısın. Türkçe sohbet dilinde yazılan mesajları JSON formatında ayrıştırırsın.

Şirket: Pena Medya
Bankalar: Ziraat, Halk, Vakıf, Kuveyt
Ortaklar: Raşit, Ömer
KDV: %20 (faturalar KDV hariç girilir)

SADECE JSON döndür, başka hiçbir şey yazma:

{
  "islem": "gelir|gider|fatura_kes|fatura_odendi|fatura_geldi|borc_odendi|avans|iade|transfer|bakiye_gir|sozlesme|musteri_ekle|tedarikci_ekle|firma_raporu|ozet|anlik_durum|kasa|alacaklar|borclar|ortaklar|proje|detay|bilinmiyor",
  "tutar": 45000,
  "aciklama": "açıklama",
  "musteri": "müşteri adı",
  "tedarikci": "tedarikçi adı",
  "firma": "firma adı (rapor için)",
  "firma_turu": "musteri veya tedarikci",
  "notlar": "firma hakkında not",
  "proje": "proje adı",
  "sozlesme_id": null,
  "kategori": "kategori",
  "banka": null,
  "hedef_banka": null,
  "kisi": "Raşit veya Ömer",
  "vade_tarihi": null,
  "tarih": null,
  "tarih_baslangic": null,
  "tarih_bitis": null,
  "sozlesme_tutari": null,
  "sozlesme_bitis": null,
  "fatura_id": null,
  "onay_mesaji": "onay metni"
}

KRİTİK İŞLEM TESPİTİ KURALLARI:

FATURA KES vs FATURA GELDİ:
- "fatura kestim / kestik / kesdik" → fatura_kes (BİZ kestik, ALACAK oluşur)
- "bana fatura kesti / bize fatura kesti / fatura geldi / fatura aldık" → fatura_geldi (BİZE kestiler, BORÇ oluşur)
- "X firması bize fatura kesti" → fatura_geldi
- "X firmasına fatura kestim" → fatura_kes

GELİR vs FATURA TAHSİLATI:
- "ödeme geldi / para geldi / tahsilat yapıldı / X ödedi" → fatura_odendi (alacaktan düşer)
- "gelir var / nakit geldi" → gelir (direkt kasa hareketi)

DİĞER İŞLEMLER:
- "avans verdim / avans aldı" → avans
- "iade etti / geri ödedi" → iade  
- "X'ten Y'ye transfer / havale" → transfer
- "X'e ödeme yaptık / X'e borç ödedik / X faturasını ödedik" → borc_odendi (tedarikçiye ödeme)
- "X bakiyesi N bin / başlangıç bakiyesi" → bakiye_gir
- "sözleşme imzaladık / anlaşma yaptık" → sozlesme
- "Jetlink ekle tedarikçi / X müşteri olarak kaydet" → musteri_ekle veya tedarikci_ekle
- "Jetlink raporu / X ile ne kadar işlem yaptık" → firma_raporu
- "özet ver" → ozet
- "anlık durum / durum nedir / genel durum" → anlik_durum
- "kasada ne var / banka bakiyeleri" → kasa
- "alacaklarımız" → alacaklar
- "borçlarımız" → borclar
- "Raşit/Ömer bakiye" → ortaklar
- "proje durumu / X projesi" → proje
- "X detayı / geçmiş / hareketler" → detay

BANKA KURALI: Banka bilgisi mesajda açıkça yazıyorsa doldur, yoksa null bırak (bot soracak).
TUTAR: Her zaman sayı (45000 gibi, "45 bin" değil).
TARİH: Bugün 2026-06-01. "dün"=2026-05-31, "geçen ay"=Mayıs 2026.
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

# ============ DURUM YÖNETİMİ ============
bekleyen = {}
sifirlama_bekleyen = set()
ozet_bekleyen = {}
vade_bekleyen = {}
banka_bekleyen = {}
fatura_secim_bekleyen = {}

# ============ İŞLEM FONKSİYONLARI ============
async def islem_yap(parsed, update):
    islem = parsed.get("islem")
    tutar = parsed.get("tutar")
    bugun = parsed.get("tarih") or date.today().isoformat()
    banka_adi = parsed.get("banka")
    banka_id, banka_ad = await banka_id_bul(banka_adi)

    if islem == "gelir":
        await sb_post("hesap_hareketleri", {
            "tarih": bugun, "tur": "gelir", "tutar": tutar,
            "aciklama": parsed.get("aciklama", ""),
            "karsi_taraf": parsed.get("musteri", ""),
            "proje_adi": parsed.get("proje", ""),
            "banka_hesabi_id": banka_id,
            "kategori": parsed.get("kategori", "gelir")
        })
        return f"Kaydedildi!\n\n{para_formatla(tutar)} gelir\nBanka: {banka_ad}\n{parsed.get('aciklama','')}"

    elif islem == "gider":
        await sb_post("hesap_hareketleri", {
            "tarih": bugun, "tur": "gider", "tutar": tutar,
            "aciklama": parsed.get("aciklama", ""),
            "karsi_taraf": parsed.get("tedarikci", ""),
            "proje_adi": parsed.get("proje", ""),
            "banka_hesabi_id": banka_id,
            "kategori": parsed.get("kategori", "gider")
        })
        return f"Kaydedildi!\n\n{para_formatla(tutar)} gider\nBanka: {banka_ad}\n{parsed.get('aciklama','')}"

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
        return (f"Fatura Kesildi! (Alacak olustu)\n\n"
                f"No: {fno}\nMusteri: {parsed.get('musteri','')}\n"
                f"KDV haric: {para_formatla(tutar)}\nToplam: {para_formatla(toplam)}\n"
                f"Vade: {parsed.get('vade_tarihi') or 'Belirtilmedi'}\n\n"
                f"NOT: Para geldiginde 'odeme aldik' yaz.")

    elif islem == "fatura_geldi":
        kdv = round(tutar * 0.20, 2)
        toplam = round(tutar * 1.20, 2)
        # Tedarikçiyi kaydet/güncelle
        tedarikci_adi = parsed.get("tedarikci", "")
        tedarikci_id = None
        if tedarikci_adi:
            mevcut = await sb_get("tedarikciler", f"?ad=ilike.*{tedarikci_adi}*")
            if isinstance(mevcut, list) and mevcut:
                tedarikci_id = mevcut[0]["id"]
            else:
                yeni = await sb_post("tedarikciler", {"ad": tedarikci_adi})
                if isinstance(yeni, list) and yeni:
                    tedarikci_id = yeni[0]["id"]

        await sb_post("gelen_faturalar", {
            "tedarikci_id": tedarikci_id,
            "tedarikci_adi": tedarikci_adi,
            "kategori": parsed.get("kategori", "genel"),
            "kdv_haric_tutar": tutar,
            "fatura_tarihi": bugun,
            "vade_tarihi": parsed.get("vade_tarihi"),
            "durum": "bekliyor",
            "aciklama": parsed.get("aciklama", "")
        })
        return (f"Gelen Fatura Kaydedildi! (Borc olustu)\n\n"
                f"Tedarikci: {tedarikci_adi}\n"
                f"KDV haric: {para_formatla(tutar)}\nToplam: {para_formatla(toplam)}\n"
                f"Vade: {parsed.get('vade_tarihi') or 'Belirtilmedi'}")

    elif islem == "fatura_odendi":
        musteri = parsed.get("musteri", "")
        fatura_id = parsed.get("_secilen_fatura_id")
        if not fatura_id:
            faturalar = await sb_get("kesilen_faturalar", f"?musteri_adi=ilike.*{musteri}*&durum=in.(bekliyor,kismi)&order=fatura_tarihi.desc")
            if not faturalar or not isinstance(faturalar, list):
                return f"{musteri} adina acik fatura bulunamadi."
            fatura = faturalar[0]
            fatura_id = fatura["id"]
        else:
            fatura_result = await sb_get("kesilen_faturalar", f"?id=eq.{fatura_id}")
            fatura = fatura_result[0] if isinstance(fatura_result, list) and fatura_result else {}

        fatura_toplam = float(fatura.get("toplam_tutar") or 0)
        mevcut_tahsil = float(fatura.get("tahsil_edilen") or 0)
        yeni_tahsil = mevcut_tahsil + tutar
        yeni_durum = "odendi" if yeni_tahsil >= fatura_toplam else "kismi"
        kalan = max(0, fatura_toplam - yeni_tahsil)

        await sb_patch("kesilen_faturalar", f"?id=eq.{fatura_id}", {
            "durum": yeni_durum,
            "odeme_tarihi": bugun,
            "tahsil_edilen": yeni_tahsil
        })
        await sb_post("hesap_hareketleri", {
            "tarih": bugun, "tur": "gelir", "tutar": tutar,
            "aciklama": f"{musteri} fatura odemesi",
            "karsi_taraf": musteri, "banka_hesabi_id": banka_id,
            "kesilen_fatura_id": fatura_id,
            "kategori": "fatura tahsilati"
        })
        durum_text = "KAPANDI" if yeni_durum == "odendi" else f"KISMI - Kalan: {para_formatla(kalan)}"
        return (f"Tahsilat kaydedildi!\n\n"
                f"Musteri: {musteri}\nTutar: {para_formatla(tutar)}\n"
                f"Banka: {banka_ad}\nFatura: {durum_text}")

    elif islem == "borc_odendi":
        tedarikci = parsed.get("tedarikci", "")
        fatura_guncellendi = False
        kalan_text = ""
        if tedarikci:
            faturalar = await sb_get("gelen_faturalar", f"?tedarikci_adi=ilike.*{tedarikci}*&durum=in.(bekliyor,kismi)&order=fatura_tarihi.desc")
            if faturalar and isinstance(faturalar, list):
                fatura = faturalar[0]
                fatura_id = fatura["id"]
                fatura_toplam = float(fatura.get("toplam_tutar") or 0)
                mevcut_odenen = float(fatura.get("odenen") or 0)
                yeni_odenen = mevcut_odenen + tutar
                yeni_durum = "odendi" if yeni_odenen >= fatura_toplam else "kismi"
                kalan = max(0, fatura_toplam - yeni_odenen)
                await sb_patch("gelen_faturalar", f"?id=eq.{fatura_id}", {
                    "durum": yeni_durum,
                    "odeme_tarihi": bugun,
                    "odenen": yeni_odenen
                })
                fatura_guncellendi = True
                kalan_text = "KAPANDI" if yeni_durum == "odendi" else f"Kalan: {para_formatla(kalan)}"
        await sb_post("hesap_hareketleri", {
            "tarih": bugun, "tur": "gider", "tutar": tutar,
            "aciklama": parsed.get("aciklama", f"{tedarikci} borc odemesi"),
            "karsi_taraf": tedarikci,
            "banka_hesabi_id": banka_id, "kategori": "borc odemesi"
        })
        sonuc = f"Borc odemesi kaydedildi!\n\n{para_formatla(tutar)}\nBanka: {banka_ad}\nTedarikci: {tedarikci}"
        if fatura_guncellendi:
            sonuc += f"\nFatura: {kalan_text}"
        return sonuc

    elif islem == "avans":
        kisi = parsed.get("kisi", "")
        await sb_post("ic_hareketler", {
            "kisi": kisi, "tur": "avans", "tutar": tutar,
            "tarih": bugun, "aciklama": parsed.get("aciklama", ""), "durum": "acik"
        })
        await sb_post("hesap_hareketleri", {
            "tarih": bugun, "tur": "gider", "tutar": tutar,
            "aciklama": f"{kisi} avans",
            "karsi_taraf": kisi, "banka_hesabi_id": banka_id,
            "kategori": "ic hareket"
        })
        return f"{kisi}'e {para_formatla(tutar)} avans kaydedildi.\nBanka: {banka_ad}"

    elif islem == "iade":
        kisi = parsed.get("kisi", "")
        await sb_post("ic_hareketler", {
            "kisi": kisi, "tur": "iade", "tutar": tutar,
            "tarih": bugun, "aciklama": parsed.get("aciklama", ""), "durum": "kapandi"
        })
        await sb_post("hesap_hareketleri", {
            "tarih": bugun, "tur": "gelir", "tutar": tutar,
            "aciklama": f"{kisi} iade",
            "karsi_taraf": kisi, "banka_hesabi_id": banka_id,
            "kategori": "ic hareket"
        })
        return f"{kisi} {para_formatla(tutar)} iade kaydedildi.\nBanka: {banka_ad}"

    elif islem == "transfer":
        hedef_id, hedef_ad = await banka_id_bul(parsed.get("hedef_banka"))
        await sb_post("hesap_hareketleri", {
            "tarih": bugun, "tur": "transfer", "tutar": tutar,
            "aciklama": f"{banka_ad} → {hedef_ad} transfer",
            "banka_hesabi_id": banka_id,
            "transfer_hedef_banka_id": hedef_id,
            "kategori": "transfer"
        })
        return f"Transfer kaydedildi!\n\n{banka_ad} → {hedef_ad}\n{para_formatla(tutar)}"

    elif islem == "bakiye_gir":
        if not banka_id:
            return "Banka bulunamadi. Ziraat, Halk, Vakif veya Kuveyt yaz."
        await sb_post("banka_bakiye_giris", {
            "banka_hesabi_id": banka_id, "tutar": tutar,
            "tarih": bugun, "aciklama": "Baslangic bakiyesi"
        })
        return f"{banka_ad} baslangic bakiyesi: {para_formatla(tutar)} kaydedildi."

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
        return (f"Sozlesme kaydedildi!\n\n"
                f"Musteri: {parsed.get('musteri','')}\n"
                f"Proje: {parsed.get('proje','')}\n"
                f"Toplam: {para_formatla(parsed.get('sozlesme_tutari') or tutar)}")

    elif islem == "musteri_ekle":
        firma = parsed.get("musteri") or parsed.get("firma", "")
        mevcut = await sb_get("musteriler", f"?ad=ilike.*{firma}*")
        if isinstance(mevcut, list) and mevcut:
            return f"{firma} zaten kayitli."
        await sb_post("musteriler", {"ad": firma, "notlar": parsed.get("notlar", "")})
        return f"{firma} musteri olarak kaydedildi."

    elif islem == "tedarikci_ekle":
        firma = parsed.get("tedarikci") or parsed.get("firma", "")
        mevcut = await sb_get("tedarikciler", f"?ad=ilike.*{firma}*")
        if isinstance(mevcut, list) and mevcut:
            return f"{firma} zaten kayitli."
        await sb_post("tedarikciler", {"ad": firma, "notlar": parsed.get("notlar", "")})
        return f"{firma} tedarikci olarak kaydedildi."

    elif islem == "firma_raporu":
        return await firma_raporu_getir(parsed.get("firma") or parsed.get("musteri") or parsed.get("tedarikci", ""))

    elif islem == "anlik_durum":
        return await anlik_durum_getir()

    elif islem == "kasa":
        return await kasa_getir()

    elif islem == "alacaklar":
        return await alacaklar_getir()

    elif islem == "borclar":
        return await borclar_getir()

    elif islem == "ortaklar":
        return await ortaklar_getir()

    elif islem == "proje":
        return await proje_getir(parsed.get("proje") or parsed.get("musteri", ""))

    elif islem == "detay":
        return await detay_getir(parsed)

    else:
        return parsed.get("onay_mesaji", "Anlayamadim.")

# ============ SORGULAR ============
async def anlik_durum_getir():
    bankalar = await sb_get("banka_bakiyeleri")
    alacaklar = await sb_get("bekleyen_alacaklar")
    borclar = await sb_get("bekleyen_borclar")
    ic = await sb_get("ic_bakiye")

    toplam_kasa = 0
    kasa_text = "KASA\n"
    if isinstance(bankalar, list):
        for b in bankalar:
            bak = float(b.get("bakiye") or 0)
            toplam_kasa += bak
            kasa_text += f"{b['banka']}: {para_formatla(bak)}\n"
    kasa_text += f"Toplam: {para_formatla(toplam_kasa)}"

    toplam_alacak = sum(float(r.get("kalan_alacak") or r.get("toplam_tutar") or 0) for r in alacaklar) if isinstance(alacaklar, list) else 0
    geciken_alacak = sum(float(r.get("kalan_alacak") or r.get("toplam_tutar") or 0) for r in alacaklar if isinstance(alacaklar, list) and (r.get("gecikme_gunu") or 0) > 0)
    toplam_borc = sum(float(r.get("kalan_borc") or r.get("toplam_tutar") or 0) for r in borclar) if isinstance(borclar, list) else 0
    net = toplam_kasa + toplam_alacak - toplam_borc

    ortak_text = "ORTAKLAR\n"
    if isinstance(ic, list):
        for r in ic:
            nb = float(r.get("net_borc") or 0)
            ortak_text += f"{r['kisi']}: {para_formatla(nb)} borclu\n" if nb > 0 else f"{r['kisi']}: Temiz\n"

    return (f"ANLIK DURUM\n\n"
            f"{kasa_text}\n\n"
            f"ALACAKLAR\n"
            f"Toplam Kalan: {para_formatla(toplam_alacak)}\n"
            f"Gecikmiş: {para_formatla(geciken_alacak)}\n\n"
            f"BORCLAR\n"
            f"Toplam: {para_formatla(toplam_borc)}\n\n"
            f"NET FINANSAL DURUM: {para_formatla(net)}\n\n"
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
        metin += f"{b['banka']}: {para_formatla(bak)}\n"
    metin += f"\nToplam: {para_formatla(toplam)}"
    return metin

async def alacaklar_getir():
    data = await sb_get("bekleyen_alacaklar")
    if not isinstance(data, list) or not data:
        return "Bekleyen alacak yok!"
    metin = f"BEKLEYEN ALACAKLAR ({len(data)} fatura)\n\n"
    for r in data:
        toplam_t = float(r.get("toplam_tutar") or 0)
        kalan = float(r.get("kalan_alacak") or toplam_t)
        tahsil = float(r.get("tahsil_edilen") or 0)
        g = r.get("gecikme_gunu") or 0
        gecikme = f" ⚠ {g} gun gecikti" if g and g > 0 else ""
        vade = r.get("vade_tarihi") or "Vade yok"
        metin += f"{r.get('fatura_no','?')} - {r.get('musteri_adi','?')}\n"
        if tahsil > 0:
            metin += f"  Toplam: {para_formatla(toplam_t)} | Tahsil: {para_formatla(tahsil)} | Kalan: {para_formatla(kalan)}\n"
        else:
            metin += f"  {para_formatla(kalan)} | Vade: {vade}{gecikme}\n"
        metin += "\n"
    toplam_kalan = sum(float(r.get("kalan_alacak") or r.get("toplam_tutar") or 0) for r in data)
    metin += f"Toplam Kalan: {para_formatla(toplam_kalan)}"
    return metin

async def borclar_getir():
    data = await sb_get("bekleyen_borclar")
    if not isinstance(data, list) or not data:
        return "Bekleyen borc yok!"
    metin = f"BEKLEYEN BORCLAR ({len(data)} fatura)\n\n"
    for r in data:
        toplam_t = float(r.get("toplam_tutar") or 0)
        kalan = float(r.get("kalan_borc") or toplam_t)
        odenen = float(r.get("odenen") or 0)
        g = r.get("gecikme_gunu") or 0
        gecikme = f" ⚠ {g} gun gecikti" if g and g > 0 else ""
        vade = r.get("vade_tarihi") or "Vade yok"
        metin += f"{r.get('tedarikci_adi','?')} | {r.get('kategori','')}\n"
        if odenen > 0:
            metin += f"  Toplam: {para_formatla(toplam_t)} | Odenen: {para_formatla(odenen)} | Kalan: {para_formatla(kalan)}\n"
        else:
            metin += f"  {para_formatla(kalan)} | Vade: {vade}{gecikme}\n"
        metin += "\n"
    toplam_kalan = sum(float(r.get("kalan_borc") or r.get("toplam_tutar") or 0) for r in data)
    metin += f"Toplam Kalan: {para_formatla(toplam_kalan)}"
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
        metin += f"  Toplam avans/borc: {para_formatla(tb)}\n"
        metin += f"  Toplam iade: {para_formatla(ti)}\n"
        metin += f"  Net: {para_formatla(nb)}\n\n"
    if isinstance(detay, list) and detay:
        metin += "SON HAREKETLER\n"
        for r in detay[:10]:
            metin += f"{r.get('tarih','?')} | {r.get('kisi','?')} | {r.get('tur','?')} | {para_formatla(r.get('tutar',0))}\n"
    return metin

async def proje_getir(proje_adi):
    if not proje_adi:
        sozlesmeler = await sb_get("proje_ozet")
        if not isinstance(sozlesmeler, list) or not sozlesmeler:
            return "Kayitli sozlesme/proje yok."
        metin = "PROJELER\n\n"
        for s in sozlesmeler:
            metin += f"{s.get('musteri_adi','?')} - {s.get('proje_adi','?')}\n"
            metin += f"  Sozlesme: {para_formatla(s.get('sozlesme_tutari',0))}\n"
            metin += f"  Faturalandirilan: {para_formatla(s.get('faturalandirilan',0))}\n"
            metin += f"  Kalan: {para_formatla(s.get('kalan_faturalanacak',0))}\n\n"
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
                 f"Sozlesme: {para_formatla(s.get('sozlesme_tutari',0))}\n"
                 f"Faturalandirilan: {para_formatla(s.get('faturalandirilan',0))}\n"
                 f"Tahsil edilen: {para_formatla(s.get('tahsil_edilen',0))}\n"
                 f"Kalan faturalanacak: {para_formatla(s.get('kalan_faturalanacak',0))}\n\n")
        if isinstance(faturalar, list) and faturalar:
            metin += "FATURALAR\n"
            for f in faturalar:
                metin += f"{f.get('fatura_no','?')} | {para_formatla(f.get('toplam_tutar',0))} | {f.get('durum','?')} | Vade: {f.get('vade_tarihi','?')}\n"
        return metin

async def detay_getir(parsed):
    musteri = parsed.get("musteri", "")
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
        metin += f"{r.get('tarih','?')} | {tur}{para_formatla(t)} | {r.get('aciklama','')}\n"
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
            f"Gelir: {para_formatla(gelir)}\n"
            f"Gider: {para_formatla(gider)}\n"
            f"Net: {para_formatla(net)}")

async def firma_raporu_getir(firma_adi):
    if not firma_adi:
        return "Firma adi belirtilmedi."
    metin = f"FIRMA RAPORU: {firma_adi}\n\n"

    # Kesilen faturalar (müşteri)
    kf = await sb_get("kesilen_faturalar", f"?musteri_adi=ilike.*{firma_adi}*&order=fatura_tarihi.desc")
    if isinstance(kf, list) and kf:
        toplam_kf = sum(float(r.get("toplam_tutar",0)) for r in kf)
        tahsil_kf = sum(float(r.get("tahsil_edilen",0)) for r in kf)
        metin += f"KESİLEN FATURALAR ({len(kf)} adet)\n"
        metin += f"Toplam: {para_formatla(toplam_kf)} | Tahsil: {para_formatla(tahsil_kf)} | Kalan: {para_formatla(toplam_kf-tahsil_kf)}\n\n"
        for r in kf[:5]:
            metin += f"  {r.get('fatura_no','?')} | {para_formatla(r.get('toplam_tutar',0))} | {r.get('durum','?')}\n"
        if len(kf) > 5:
            metin += f"  ...ve {len(kf)-5} fatura daha\n"
        metin += "\n"

    # Gelen faturalar (tedarikçi)
    gf = await sb_get("gelen_faturalar", f"?tedarikci_adi=ilike.*{firma_adi}*&order=fatura_tarihi.desc")
    if isinstance(gf, list) and gf:
        toplam_gf = sum(float(r.get("toplam_tutar",0)) for r in gf)
        metin += f"GELEN FATURALAR ({len(gf)} adet)\n"
        metin += f"Toplam Borc: {para_formatla(toplam_gf)}\n\n"
        for r in gf[:5]:
            metin += f"  {r.get('fatura_tarihi','?')} | {para_formatla(r.get('toplam_tutar',0))} | {r.get('durum','?')}\n"
        metin += "\n"

    # Hesap hareketleri
    hh = await sb_get("hesap_hareketleri", f"?karsi_taraf=ilike.*{firma_adi}*&order=tarih.desc&limit=10")
    if isinstance(hh, list) and hh:
        metin += f"SON HAREKETLER\n"
        for r in hh:
            tur = "+" if r.get("tur") == "gelir" else "-"
            metin += f"  {r.get('tarih','?')} | {tur}{para_formatla(r.get('tutar',0))} | {r.get('aciklama','')}\n"

    if not (isinstance(kf, list) and kf) and not (isinstance(gf, list) and gf):
        metin += "Bu firma icin kayit bulunamadi."

    return metin

async def excel_export(update):
    data = await sb_get("hesap_hareketleri", "?order=tarih.desc")
    if not isinstance(data, list) or not data:
        await update.message.reply_text("Disa aktarilacak veri yok.")
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

# ============ KOMUTLAR ============
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

# ============ ANA MESAJ İŞLEYİCİ ============
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

    # Fatura seçimi bekleniyor (hangi faturanın tahsilatı)
    if uid in fatura_secim_bekleyen:
        bilgi = fatura_secim_bekleyen.pop(uid)
        parsed = bilgi["parsed"]
        faturalar = bilgi["faturalar"]
        try:
            secim = int(mesaj.strip()) - 1
            if 0 <= secim < len(faturalar):
                parsed["_secilen_fatura_id"] = faturalar[secim]["id"]
                bekleyen[uid] = parsed
                fatura = faturalar[secim]
                await update.message.reply_text(
                    f"{fatura.get('fatura_no','?')} - {para_formatla(fatura.get('toplam_tutar',0))} faturası secildi.\n\nKaydedeyim mi? (Evet/Hayir)"
                )
            else:
                await update.message.reply_text("Gecersiz secim. Iptal edildi.")
        except:
            if mesaj.lower() in ["hayir", "iptal"]:
                await update.message.reply_text("Iptal edildi.")
            else:
                await update.message.reply_text("Sayi yazin (1, 2, 3...) veya 'hayir' deyin.")
                fatura_secim_bekleyen[uid] = bilgi
        return

    # Vade tarihi bekleniyor
    if uid in vade_bekleyen:
        parsed = vade_bekleyen.pop(uid)
        if mesaj.lower() in ["vade yok", "atla", "-"]:
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
            f"{parsed.get('musteri') or parsed.get('tedarikci','')} - {para_formatla(parsed.get('tutar',0))}\nVade: {vade_str}\n\nKaydedeyim mi? (Evet/Hayir)"
        )
        return

    # Banka bekleniyor
    if uid in banka_bekleyen:
        parsed = banka_bekleyen.pop(uid)
        parsed["banka"] = mesaj.strip()
        bekleyen[uid] = parsed
        banka_id, banka_ad = await banka_id_bul(mesaj.strip())
        islem_text = f"{para_formatla(parsed.get('tutar',0))} - Banka: {banka_ad or mesaj}"
        await update.message.reply_text(f"{islem_text}\n\nKaydedeyim mi? (Evet/Hayir)")
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

    # Vade kontrolü (fatura_kes ve fatura_geldi için)
    if islem in ["fatura_kes", "fatura_geldi"] and not parsed.get("vade_tarihi"):
        vade_bekleyen[uid] = parsed
        taraf = parsed.get("musteri") or parsed.get("tedarikci", "")
        await update.message.reply_text(
            f"{taraf} icin {para_formatla(parsed.get('tutar',0))} fatura.\n\n"
            f"Vade tarihi nedir? (Ornek: 15 Temmuz, 2026-07-15)\n"
            f"Atlamak icin: 'vade yok' yaz"
        )
        return

    # Banka kontrolü (para hareketi olan her işlem için)
    para_hareketi = ["gelir", "gider", "avans", "iade", "borc_odendi", "transfer", "fatura_odendi"]
    if islem in para_hareketi and not parsed.get("banka"):
        banka_bekleyen[uid] = parsed
        if islem in ["gelir", "fatura_odendi", "iade"]:
            await update.message.reply_text(f"{para_formatla(parsed.get('tutar',0))} hangi bankaya geldi? (Ziraat, Halk, Vakif, Kuveyt)")
        elif islem == "transfer":
            await update.message.reply_text(f"Hangi bankadan transfer? (Ziraat, Halk, Vakif, Kuveyt)")
        else:
            await update.message.reply_text(f"{para_formatla(parsed.get('tutar',0))} hangi bankadan odendi? (Ziraat, Halk, Vakif, Kuveyt)")
        return

    # Fatura tahsilatı: hangi fatura?
    if islem == "fatura_odendi":
        musteri = parsed.get("musteri", "")
        faturalar = await sb_get("kesilen_faturalar", f"?musteri_adi=ilike.*{musteri}*&durum=in.(bekliyor,kismi)&order=fatura_tarihi.desc")
        if isinstance(faturalar, list) and len(faturalar) > 1:
            metin = f"{musteri} adina {len(faturalar)} acik fatura var. Hangisi?\n\n"
            for i, f in enumerate(faturalar[:5]):
                kalan = float(f.get("toplam_tutar",0)) - float(f.get("tahsil_edilen",0))
                metin += f"{i+1}. {f.get('fatura_no','?')} | {para_formatla(kalan)} kalan | Vade: {f.get('vade_tarihi','?')}\n"
            metin += "\nSayi yaz (1, 2...) veya 'hayir' de iptal et."
            fatura_secim_bekleyen[uid] = {"parsed": parsed, "faturalar": faturalar}
            await update.message.reply_text(metin)
            return

    # Özet → tarih sor
    if islem == "ozet":
        ozet_bekleyen[uid] = True
        await update.message.reply_text("Hangi tarih araligi? (Ornek: Haziran 2026, bu ay, 1-15 Haziran)")
        return

    # Direkt sorgular
    if islem in ["anlik_durum", "kasa", "alacaklar", "borclar", "ortaklar", "proje", "detay", "firma_raporu"]:
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
            parsed.get("onay_mesaji",
                "Anlayamadim.\n\nOrnekler:\n"
                "- TRTden 45 bin geldi Ziraate\n"
                "- Gebeye 38 bin fatura kestim\n"
                "- Jetlink bize 110 bin fatura kesti\n"
                "- Anlik durum\n"
                "- Kasa bakiyeleri")
        )

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("sifirla", sifirla_komutu))
    app.add_handler(CommandHandler("excel", excel_komutu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mesaj_isle))
    print("Pena Finans Botu v3 basladi...")
    app.run_polling(stop_signals=None)

if __name__ == "__main__":
    main()

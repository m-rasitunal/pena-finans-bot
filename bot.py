import os
import logging
from datetime import date
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import httpx
import json

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")

logging.basicConfig(level=logging.WARNING)

SYSTEM_PROMPT = """Sen Pena Medya şirketinin finansal asistanısın. Kullanıcı sana Türkçe, sohbet dilinde finansal işlemler anlatır, sen bunları JSON formatında ayrıştırırsın.

Şirket bilgileri:
- Banka hesapları: Ziraat Bankası, Halk Bankası, VakıfBank, Kuveyt Türk
- Ortaklar: Raşit, Ömer
- KDV oranı: %20 (her zaman)

Kullanıcı mesajını analiz et ve SADECE şu JSON formatında cevap ver, başka hiçbir şey yazma:

{
  "islem": "gelir | gider | fatura_kes | fatura_geldi | avans | iade | ozet | alacaklar | borclar | icbakiye | bilinmiyor",
  "tutar": 45000,
  "aciklama": "TRT Rotanı Oluştur ödemesi",
  "musteri": "TRT Genel Müdürlüğü",
  "tedarikci": null,
  "proje": "Rotanı Oluştur 2. Sezon",
  "kategori": "proje geliri",
  "banka": "Ziraat",
  "kisi": "Raşit",
  "onay_mesaji": "TRT'den 45.000 TL geldi, Ziraat'a kaydedeyim mi?"
}

Örnekler:
- "TRT'den 45 bin geldi" → islem: gelir, tutar: 45000
- "Gebze'ye fatura kestim 38 bin" → islem: fatura_kes, tutar: 38000, musteri: Gebze Belediyesi
- "Muhasebeciye 2500 ödedim" → islem: gider, tutar: 2500, aciklama: muhasebe
- "Raşit'e 5 bin avans verdim" → islem: avans, tutar: 5000, kisi: Raşit
- "Bu ay nasıl gidiyoruz?" → islem: ozet
- "Kimlerden alacağımız var?" → islem: alacaklar

Eğer anlamadıysan islem: bilinmiyor yap ve onay_mesaji alanına ne anlamadığını yaz.
Tutarları her zaman sayı olarak ver (45000, 2500 gibi - "45 bin" değil).
"""

async def supabase_insert(table: str, data: dict) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            },
            json=data
        )
        return r.json()

async def supabase_select(table: str, query: str = "") -> list:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/{table}{query}",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}"
            }
        )
        return r.json()

async def claude_analiz(mesaj: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 500,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": mesaj}]
            }
        )
        data = r.json()
        text = data["content"][0]["text"].strip()
        # JSON temizle
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())

async def islem_yap(parsed: dict) -> str:
    islem = parsed.get("islem")
    tutar = parsed.get("tutar")
    bugun = date.today().isoformat()

    if islem == "gelir":
        await supabase_insert("hesap_hareketleri", {
            "tarih": bugun,
            "tur": "gelir",
            "tutar": tutar,
            "aciklama": parsed.get("aciklama", ""),
            "kategori": parsed.get("kategori", "proje geliri")
        })
        return f"✅ Kaydedildi!\n\n💰 *{tutar:,.0f} TL gelir* eklendi\n📝 {parsed.get('aciklama','')}"

    elif islem == "gider":
        await supabase_insert("hesap_hareketleri", {
            "tarih": bugun,
            "tur": "gider",
            "tutar": tutar,
            "aciklama": parsed.get("aciklama", ""),
            "kategori": parsed.get("kategori", "genel gider")
        })
        return f"✅ Kaydedildi!\n\n💸 *{tutar:,.0f} TL gider* eklendi\n📝 {parsed.get('aciklama','')}"

    elif islem == "fatura_kes":
        kdv = round(tutar * 0.20, 2)
        toplam = round(tutar * 1.20, 2)
        result = await supabase_insert("kesilen_faturalar", {
            "fatura_no": "",
            "musteri_adi": parsed.get("musteri", ""),
            "proje_adi": parsed.get("proje", ""),
            "kdv_haric_tutar": tutar,
            "fatura_tarihi": bugun,
            "durum": "bekliyor"
        })
        fno = result[0].get("fatura_no", "PENA-????") if isinstance(result, list) and result else "PENA-????"
        return (
            f"🧾 *Fatura Kesildi!*\n\n"
            f"📄 No: `{fno}`\n"
            f"🏢 Müşteri: {parsed.get('musteri','')}\n"
            f"📁 Proje: {parsed.get('proje','')}\n"
            f"💰 KDV hariç: {tutar:,.0f} TL\n"
            f"➕ KDV: {kdv:,.0f} TL\n"
            f"💳 Toplam: *{toplam:,.0f} TL*"
        )

    elif islem == "fatura_geldi":
        kdv = round(tutar * 0.20, 2)
        toplam = round(tutar * 1.20, 2)
        await supabase_insert("gelen_faturalar", {
            "tedarikci_adi": parsed.get("tedarikci", ""),
            "kategori": parsed.get("kategori", "genel"),
            "kdv_haric_tutar": tutar,
            "fatura_tarihi": bugun,
            "durum": "bekliyor"
        })
        return (
            f"📋 *Gelen Fatura Kaydedildi!*\n\n"
            f"🏢 {parsed.get('tedarikci','')}\n"
            f"💰 KDV hariç: {tutar:,.0f} TL\n"
            f"💳 Toplam: *{toplam:,.0f} TL*"
        )

    elif islem == "avans":
        kisi = parsed.get("kisi", "")
        await supabase_insert("ic_hareketler", {
            "kisi": kisi,
            "tur": "avans",
            "tutar": tutar,
            "tarih": bugun,
            "aciklama": parsed.get("aciklama", ""),
            "durum": "acik"
        })
        return f"✅ *{kisi}'e {tutar:,.0f} TL avans* kaydedildi."

    elif islem == "iade":
        kisi = parsed.get("kisi", "")
        await supabase_insert("ic_hareketler", {
            "kisi": kisi,
            "tur": "iade",
            "tutar": tutar,
            "tarih": bugun,
            "aciklama": parsed.get("aciklama", ""),
            "durum": "kapandi"
        })
        return f"✅ *{kisi} {tutar:,.0f} TL iade* kaydedildi."

    elif islem == "ozet":
        data = await supabase_select("aylik_ozet", "?limit=1")
        if not data:
            return "📊 Henüz kayıtlı hareket yok."
        r = data[0]
        gelir = float(r.get("toplam_gelir") or 0)
        gider = float(r.get("toplam_gider") or 0)
        net = float(r.get("net_kar") or 0)
        emoji = "📈" if net >= 0 else "📉"
        return (
            f"📊 *Bu Ay Özet*\n\n"
            f"💰 Gelir: {gelir:,.0f} TL\n"
            f"💸 Gider: {gider:,.0f} TL\n"
            f"{'─'*20}\n"
            f"{emoji} Net: *{net:,.0f} TL*"
        )

    elif islem == "alacaklar":
        data = await supabase_select("bekleyen_alacaklar")
        if not data:
            return "✅ Bekleyen alacak yok!"
        toplam = sum(float(r.get("toplam_tutar") or 0) for r in data)
        metin = f"⏳ *Bekleyen Alacaklar* ({len(data)} fatura)\n\n"
        for r in data[:8]:
            t = float(r.get("toplam_tutar") or 0)
            metin += f"• {r.get('musteri_adi','?')} — *{t:,.0f} TL*\n"
        metin += f"\n💳 Toplam: *{toplam:,.0f} TL*"
        return metin

    elif islem == "borclar":
        data = await supabase_select("bekleyen_borclar")
        if not data:
            return "✅ Bekleyen borç yok!"
        toplam = sum(float(r.get("toplam_tutar") or 0) for r in data)
        metin = f"💸 *Bekleyen Borçlar* ({len(data)} fatura)\n\n"
        for r in data[:8]:
            t = float(r.get("toplam_tutar") or 0)
            metin += f"• {r.get('tedarikci_adi','?')} — *{t:,.0f} TL*\n"
        metin += f"\n💳 Toplam: *{toplam:,.0f} TL*"
        return metin

    elif islem == "icbakiye":
        data = await supabase_select("ic_bakiye")
        if not data:
            return "📊 İç hareket kaydı yok."
        metin = "👥 *İç Bakiye*\n\n"
        for r in data:
            net = float(r.get("net_borc") or 0)
            emoji = "🔴" if net > 0 else "🟢"
            metin += f"{emoji} *{r['kisi']}*: {net:,.0f} TL\n"
        return metin

    else:
        return parsed.get("onay_mesaji", "Anlayamadım, biraz daha açıklar mısın?")

# Onay bekleme sistemi
bekleyen = {}

async def mesaj_isle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kullanici_id = update.effective_user.id
    mesaj = update.message.text.strip()

    # Sıfırlama onayı mı?
    if await sifirla_onay(update, context):
        return

    # Onay cevabı mı?
    if kullanici_id in bekleyen:
        parsed = bekleyen.pop(kullanici_id)
        if mesaj.lower() in ["evet", "e", "yes", "tamam", "ok", "kaydet", "✅"]:
            await update.message.reply_text("⏳ Kaydediliyor...")
            sonuc = await islem_yap(parsed)
            await update.message.reply_text(sonuc, )
        else:
            await update.message.reply_text("❌ İptal edildi, bir şey kaydedilmedi.")
        return

    await update.message.reply_text("🤔 Anlıyorum...")

    try:
        parsed = await claude_analiz(mesaj)
    except Exception as e:
        await update.message.reply_text(f"❌ Analiz hatası: {str(e)}")
        return

    islem = parsed.get("islem", "bilinmiyor")

    # Sorgu işlemleri direkt çalışır
    if islem in ["ozet", "alacaklar", "borclar", "icbakiye"]:
        sonuc = await islem_yap(parsed)
        await update.message.reply_text(sonuc, )
        return

    # Kayıt işlemleri onay ister
    if islem != "bilinmiyor":
        onay = parsed.get("onay_mesaji", "Bu işlemi kaydedeyim mi?")
        bekleyen[kullanici_id] = parsed
        await update.message.reply_text(
            f"{onay}\n\n*Evet* veya *Hayır* yaz.",
            
        )
    else:
        await update.message.reply_text(
            parsed.get("onay_mesaji", "Anlayamadım. Örnek: 'TRT'den 45 bin geldi' veya 'Bu ay nasıl gidiyoruz?'")
        )

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("sifirla", sifirla_komutu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mesaj_isle))
    print("✅ Pena Finans Botu (Sohbet Modu) başlatıldı...")
    app.run_polling(stop_signals=None)

if __name__ == "__main__":
    main()

# Sıfırlama için bekleyen onay
sifirlama_bekleyen = set()
ADMIN_ID = 6230496507

async def sifirla_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Bu komutu kullanma yetkin yok.")
        return
    sifirlama_bekleyen.add(update.effective_user.id)
    await update.message.reply_text(
        "Tüm veriler silinecek. Bu işlem geri alınamaz.\n\n"
        "Devam etmek için: EVET SIFIRLA\n"
        "İptal için: hayır"
    )

async def sifirla_onay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in sifirlama_bekleyen:
        return False
    
    mesaj = update.message.text.strip().upper()
    if mesaj == "EVET SIFIRLA":
        sifirlama_bekleyen.discard(uid)
        await update.message.reply_text("Siliniyor...")
        try:
            async with httpx.AsyncClient() as client:
                headers = {
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                    "Content-Type": "application/json"
                }
                tablolar = ["ic_hareketler", "hesap_hareketleri", "kesilen_faturalar", "gelen_faturalar", "borc_kredi"]
                for tablo in tablolar:
                    await client.delete(
                        f"{SUPABASE_URL}/rest/v1/{tablo}?id=gte.0",
                        headers=headers
                    )
                # Fatura sequence sıfırla
                await client.post(
                    f"{SUPABASE_URL}/rest/v1/rpc/sifirla_sequence",
                    headers=headers,
                    json={}
                )
            await update.message.reply_text("Tüm veriler silindi. Sistem sıfırlandı.")
        except Exception as e:
            await update.message.reply_text(f"Hata: {str(e)}\n\nManuel sıfırlama için Supabase SQL Editor'ı kullan.")
        return True
    elif mesaj in ["HAYIR", "İPTAL", "IPTAL"]:
        sifirlama_bekleyen.discard(uid)
        await update.message.reply_text("İptal edildi.")
        return True
    return False

# Pena Finans Botu — Kurulum Kılavuzu

## 1. Kendi bilgisayarında test etmek için

### Python kur (eğer yoksa)
https://python.org/downloads → Python 3.11 indir ve kur

### Terminali aç (Windows: Başlat → "cmd" yaz)
```
cd Desktop
mkdir pena-bot
cd pena-bot
```

### Dosyaları kopyala
bot.py ve requirements.txt dosyalarını bu klasöre koy.

### Kütüphaneleri kur
```
pip install -r requirements.txt
```

### Botu başlat
```
python bot.py
```

"Pena Finans Botu başlatıldı..." yazısını görürsen çalışıyor demektir.
Telegram'ı aç, @penafinans_bot'u bul, /start yaz.

---

## 2. Render.com'da 7/24 yayınlamak için

1. github.com'da ücretsiz hesap aç
2. "New repository" → isim: pena-finans-bot → Public → Create
3. Tüm dosyaları (bot.py, requirements.txt, render.yaml) yükle
4. render.com'a git → GitHub ile giriş yap
5. "New +" → "Web Service" → GitHub reposunu seç
6. Otomatik algılar, "Create Web Service" bas
7. Deploy tamamlanınca bot 7/24 çalışır

---

## Komutlar (hızlı referans)

| Komut | Örnek |
|-------|-------|
| /gelir | /gelir 45000 TRT ödemesi Ziraat |
| /gider | /gider 3200 Ofis kirası Halk |
| /faturakes | /faturakes "TRT" 45000 "Rotanı Oluştur" |
| /faturageldi | /faturageldi "Muhasebeci" 2500 muhasebe |
| /ozet | Bu ay gelir/gider |
| /alacaklar | Bekleyen alacaklar |
| /borclar | Bekleyen borçlar |
| /avans | /avans Rasit 5000 Seyahat |
| /iade | /iade Omer 2000 Kısmi iade |
| /icbakiye | Raşit ve Ömer bakiyeleri |
| /borcekle | /borcekle Ziraat 150000 Kredi |
| /borc_kredi | Tüm borç/kredi listesi |

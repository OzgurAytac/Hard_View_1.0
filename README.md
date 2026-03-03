# HardView (1.0)

HardView, Windows bilgisayarlar için hazırlanmış bir **sistem bilgisi görüntüleme** uygulamasıdır.  
PySide6 (Qt) arayüzü ile işlemci, anakart/BIOS, RAM, ekran kartı ve işletim sistemi bilgilerini tek ekranda gösterir.

## Özellikler

- **İşlemci**
  - İşlemci adı
  - Fiziksel / mantıksal çekirdek sayısı
  - Anlık frekans (GHz)
  - Toplam CPU kullanımı ve çekirdek bazlı kullanım tablosu (1 sn güncelleme)

- **Anakart / BIOS**
  - Anakart üreticisi
  - BIOS sürümü ve tarihi
  - Cihaz seri numarası (birden fazla kaynaktan doğrulama ve “dummy” seri no filtreleme)

- **RAM**
  - Toplam RAM
  - Slot bazında modül boyutu, hız (MHz), üretici, parça no / seri no

- **Ekran Kartı**
  - GPU adı
  - VRAM (varsa)
  - NVIDIA için (opsiyonel) NVML ile daha doğru VRAM okuma

- **Hakkında**
  - Uygulama sürüm bilgileri
  - İşletim sistemi (Windows 10/11 tespiti iyileştirilmiş)
  - Derleme numarası, mimari, bilgisayar adı

## Uygulama İçi Görseller

![GenelGörünüm](screenshots/1.png)
![Hakkında](screenshots/2.png)

## İndir (EXE)

- **HardView.exe (Windows):** https://github.com/OzgurAytac/Projects/Hard_View_1.0/dist//HardView.exe


## Kurulum(EXE kullanmak yerine geliştirmek isteyenler için:)

### Gereksinimler
- Windows
- Python 3.10+ (önerilir)

### Kurulum Adımları
```bash
git clone https://github.com/KULLANICI_ADIN/REPO_ADI.git
cd REPO_ADI
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python HardView.py




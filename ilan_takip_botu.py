import datetime
import json
import os
import random
import time
import urllib.parse
from typing import Any

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "BURAYA_BOT_TOKEN_YAZIN").strip()
CHAT_ID = os.getenv("CHAT_ID", "BURAYA_CHAT_ID_YAZIN").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

KAYIT_DOSYASI = os.getenv("KAYIT_DOSYASI", "gonderilenler.json")
PROJE_DOSYASI = os.getenv("PROJE_DOSYASI", "kriterler.json")
WEB_VERI_DOSYASI = os.getenv("WEB_VERI_DOSYASI", "docs/data.json")

DEFAULT_KRITERLER = {
    "anahtar_kelimeler": [
        "mühendis",
        "bilgisayar",
        "yazılım",
        "veri",
        "büro",
        "memur",
        "sözleşmeli",
        "işçi",
        "gaziantep",
        "ankara",
    ],
    "haric_kelimeler": [
        "iptal",
        "düzeltme ilanı",
        "sonuç",
        "kazananlar",
    ],
    "profil_ozeti": (
        "Türkiye'deki kamu personel/işçi/akademik alım ilanlarını takip ediyorum. "
        "İlgilendiğim anahtar kelimeler veya şehir/kadrolarla uyuşan, aktif başvuru/ilan "
        "duyurularını önceliklendir."
    ),
    "yapay_zeka_filtre_aktif": True,
    "maks_sayfa_ilan_gov_tr": 2,
}

KAYNAKLAR = [
    {
        "ad": "Resmî Gazete",
        "url": "https://www.resmigazete.gov.tr/",
        "base_url": "https://www.resmigazete.gov.tr",
        "tip": "statik",
    },
    {
        "ad": "ÇŞB Yerel Yönetimler",
        "url": "https://yerelyonetimler.csb.gov.tr/duyurular/4",
        "base_url": "https://yerelyonetimler.csb.gov.tr",
        "tip": "statik",
    },
    {
        "ad": "Kariyer Kapısı",
        "url": "https://kariyerkapisi.gov.tr/isealim",
        "base_url": "https://kariyerkapisi.gov.tr",
        "tip": "dinamik",
    },
    {
        "ad": "Kamu İlan (SBB)",
        "url": "https://kamuilan.sbb.gov.tr/",
        "base_url": "https://kamuilan.sbb.gov.tr",
        "tip": "dinamik",
    },
    {
        "ad": "İŞKUR Açık İş",
        "url": "https://esube.iskur.gov.tr/Istihdam/AcikIsIlanAra.aspx",
        "base_url": "https://esube.iskur.gov.tr",
        "tip": "dinamik",
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}


def tr_lower(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def kriterleri_yukle() -> dict[str, Any]:
    if os.path.exists(PROJE_DOSYASI):
        with open(PROJE_DOSYASI, "r", encoding="utf-8") as f:
            veri = json.load(f)
            merged = DEFAULT_KRITERLER.copy()
            merged.update(veri)
            return merged
    return DEFAULT_KRITERLER.copy()


def gecmisi_yukle() -> set[str]:
    if os.path.exists(KAYIT_DOSYASI):
        with open(KAYIT_DOSYASI, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return set(data)
    return set()


def gecmisi_kaydet(kayit_seti: set[str]) -> None:
    with open(KAYIT_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(sorted(list(kayit_seti)), f, ensure_ascii=False, indent=2)


def web_verisi_yukle() -> dict[str, Any]:
    if os.path.exists(WEB_VERI_DOSYASI):
        try:
            with open(WEB_VERI_DOSYASI, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("ilanlar"), list):
                    return data
        except Exception:
            pass
    return {"son_guncelleme": "", "toplam_ilan": 0, "ilanlar": []}


def web_verisi_kaydet(yeni_ilanlar: list[dict[str, str]], kriterler: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(WEB_VERI_DOSYASI) or ".", exist_ok=True)
    mevcut = web_verisi_yukle()
    mevcut_liste = mevcut.get("ilanlar", [])
    mevcut_anahtarlar = {f"{x.get('kaynak')}|{x.get('url')}" for x in mevcut_liste}

    eklenenler = []
    for ilan in yeni_ilanlar:
        anahtar = f"{ilan['kaynak']}|{ilan['url']}"
        if anahtar not in mevcut_anahtarlar:
            eklenenler.append(ilan)
            mevcut_anahtarlar.add(anahtar)

    birlesik = (eklenenler + mevcut_liste)[:500]
    simdi = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).strftime("%d.%m.%Y %H:%M")

    cikti = {
        "son_guncelleme": simdi,
        "toplam_ilan": len(birlesik),
        "aktif_anahtar_kelimeler": kriterler.get("anahtar_kelimeler", []),
        "ilanlar": birlesik,
    }
    with open(WEB_VERI_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(cikti, f, ensure_ascii=False, indent=2)


def url_birlestir(base_url: str, href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    if href.startswith("javascript:") or href.startswith("#") or href.startswith("mailto:"):
        return ""
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", href)


def on_filtre_uygun_mu(baslik: str, kriterler: dict[str, Any]) -> bool:
    metin = " ".join(tr_lower(baslik).split())
    if len(metin) < 12:
        return False

    haric_kelimeler = [tr_lower(k).strip() for k in kriterler.get("haric_kelimeler", []) if k.strip()]
    if any(h in metin for h in haric_kelimeler):
        return False

    anahtar_kelimeler = [tr_lower(k).strip() for k in kriterler.get("anahtar_kelimeler", []) if k.strip()]
    if not anahtar_kelimeler:
        return True
    return any(k in metin for k in anahtar_kelimeler)


def statik_kaynak_tara(kaynak: dict[str, str], kriterler: dict[str, Any]) -> list[dict[str, str]]:
    adaylar: list[dict[str, str]] = []
    ad = kaynak["ad"]
    url = kaynak["url"]
    base_url = kaynak["base_url"]

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        gorulen_url = set()
        for a in soup.find_all("a", href=True):
            baslik = " ".join(a.get_text().split())
            tam_url = url_birlestir(base_url, a["href"])
            if not tam_url or tam_url in gorulen_url:
                continue
            if on_filtre_uygun_mu(baslik, kriterler):
                gorulen_url.add(tam_url)
                adaylar.append({"kaynak": ad, "baslik": baslik, "url": tam_url})
    except Exception as e:
        print(f"[{ad}] Hata: {e}")

    return adaylar


def dinamik_kaynak_tara(kaynak: dict[str, str], kriterler: dict[str, Any], bekleme_sn: float = 4.0) -> list[dict[str, str]]:
    adaylar: list[dict[str, str]] = []
    ad = kaynak["ad"]
    url = kaynak["url"]

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=HEADERS["User-Agent"], locale="tr-TR")
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(int(bekleme_sn * 1000))

            linkler = page.eval_on_selector_all(
                "a[href]",
                """els => els.map(e => ({
                    text: (e.innerText || e.textContent || '').trim(),
                    href: e.href || ''
                }))""",
            )
            browser.close()

            gorulen_url = set()
            for item in linkler:
                baslik = " ".join((item.get("text") or "").split())
                tam_url = (item.get("href") or "").strip()
                if not tam_url.startswith("http") or tam_url in gorulen_url:
                    continue
                if on_filtre_uygun_mu(baslik, kriterler):
                    gorulen_url.add(tam_url)
                    adaylar.append({"kaynak": ad, "baslik": baslik, "url": tam_url})
    except Exception as e:
        print(f"[{ad}] Dinamik tarama hatası: {e}")

    return adaylar


def ilan_gov_tr_tara(kriterler: dict[str, Any]) -> list[dict[str, str]]:
    adaylar: list[dict[str, str]] = []
    maks_sayfa = int(kriterler.get("maks_sayfa_ilan_gov_tr", 2))

    for sayfa in range(1, maks_sayfa + 1):
        url = f"https://www.ilan.gov.tr/ilan/tum-ilanlar/personel-alimi?currentPage={sayfa}&ats=5"
        kaynak = {"ad": "İlan.gov.tr", "url": url, "base_url": "https://www.ilan.gov.tr"}
        adaylar.extend(dinamik_kaynak_tara(kaynak, kriterler, bekleme_sn=4.5))
        time.sleep(random.uniform(2.0, 4.0))

    return adaylar


def gemini_ile_degerlendir(ilan: dict[str, str], kriterler: dict[str, Any]) -> tuple[bool, str]:
    if not GEMINI_API_KEY or not kriterler.get("yapay_zeka_filtre_aktif", True):
        return True, "Anahtar kelime eşleşmesi"

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    )
    prompt = (
        "Sen bir kamu alımı eşleştirme asistanısın. Aşağıdaki ilan başlığını ve kaynağını "
        "kullanıcı profiline göre değerlendir.\n"
        f"Kullanıcı Kriterleri: {kriterler.get('profil_ozeti', '')}\n"
        f"Anahtar Kelimeler: {', '.join(kriterler.get('anahtar_kelimeler', []))}\n"
        f"Kaynak: {ilan['kaynak']}\n"
        f"Başlık: {ilan['baslik']}\n"
        f"URL: {ilan['url']}\n\n"
        "Sadece şu JSON formatında yanıt ver:\n"
        '{"uygun": true_veya_false, "kisa_not": "tek cümlelik gerekçe"}'
    )

    try:
        resp = requests.post(
            endpoint,
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        metin = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
            .strip()
        )
        temiz = metin.replace("```json", "").replace("```", "").strip()
        sonuc = json.loads(temiz)
        return bool(sonuc.get("uygun", True)), str(sonuc.get("kisa_not", "AI onayı"))
    except Exception as e:
        print(f"[Gemini] Değerlendirme yedeğe düştü: {e}")
        return True, "AI yedeği: anahtar kelime eşleşmesi"


def telegram_gonder(mesaj: str) -> bool:
    if not TELEGRAM_TOKEN or "BURAYA_" in TELEGRAM_TOKEN or not CHAT_ID or "BURAYA_" in CHAT_ID:
        print("[UYARI] TELEGRAM_TOKEN veya CHAT_ID ayarlanmamış. Mesaj konsola yazdırıldı:\n" + mesaj)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": mesaj,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, data=payload, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[Telegram] Gönderim hatası: {e}")
        return False


def ana_dongu() -> None:
    kriterler = kriterleri_yukle()
    gonderilenler = gecmisi_yukle()
    tum_adaylar: list[dict[str, str]] = []

    for kaynak in KAYNAKLAR:
        if kaynak["tip"] == "statik":
            tum_adaylar.extend(statik_kaynak_tara(kaynak, kriterler))
        else:
            tum_adaylar.extend(dinamik_kaynak_tara(kaynak, kriterler))
        time.sleep(random.uniform(1.5, 3.0))

    tum_adaylar.extend(ilan_gov_tr_tara(kriterler))

    simdi = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).strftime("%d.%m.%Y %H:%M")
    yeni_web_kayitlari: list[dict[str, str]] = []
    yeni_bildirim = 0

    for ilan in tum_adaylar:
        anahtar = f"{ilan['kaynak']}|{ilan['url']}"
        if anahtar in gonderilenler:
            continue

        uygun, not_metni = gemini_ile_degerlendir(ilan, kriterler)
        if not uygun:
            gonderilenler.add(anahtar)
            continue

        baslik_guvenli = (
            ilan["baslik"]
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        not_guvenli = (
            not_metni
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        mesaj = (
            "🚨 <b>Yeni Kamu İlanı Bulundu</b>\n"
            f"🏛️ <b>Kaynak:</b> {ilan['kaynak']}\n"
            f"📌 <b>İlan:</b> {baslik_guvenli}\n"
            f"📝 <b>Not:</b> {not_guvenli}\n"
            f"🔗 <a href='{ilan['url']}'>İlan Detayına Git</a>"
        )
        if telegram_gonder(mesaj):
            yeni_bildirim += 1

        gonderilenler.add(anahtar)
        yeni_web_kayitlari.append({
            "kaynak": ilan["kaynak"],
            "baslik": ilan["baslik"],
            "url": ilan["url"],
            "not": not_metni,
            "tarih": simdi,
        })
        time.sleep(0.5)

    gecmisi_kaydet(gonderilenler)
    web_verisi_kaydet(yeni_web_kayitlari, kriterler)
    print(f"Tarama tamamlandı. Toplam aday: {len(tum_adaylar)}, Yeni bildirim: {yeni_bildirim}")


if __name__ == "__main__":
    ana_dongu()

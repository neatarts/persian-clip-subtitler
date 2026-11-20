# persian-clip-subtitler — نسخه کوتاه فارسی

> نسخه کامل و اصلی به انگلیسی است: [README.md](README.md)

این ابزار روی یک فایل ویدیویی (یا یک لینک) زیرنویس فارسی می‌سوزاند:

1. **رونویسی** با faster-whisper (مدل لوکال، روی CPU) — تکه‌تکه و قابل ازسرگیری
2. **ترجمه** به فارسی — با فایل ترجمه خودتان (پیشنهاد می‌شود) یا خودکار با LibreTranslate
3. **رندر** — هر کپشن با فونت وزیرمتن Bold، داخل یک باکس گرد نیمه‌شفاف، حداکثر ۲۰ حرف در هر خط و ۲ خط در هر کپشن

## نصب و استفاده

```bash
git clone https://github.com/neatarts/persian-clip-subtitler.git
cd persian-clip-subtitler
pip install -e .
huggingface-cli download Systran/faster-whisper-small --local-dir models/whisper-small

pcs --input clip.mp4 --work-dir job-001 \
    --model models/whisper-small \
    --translate libretranslate
```

خروجی در `job-001` ساخته می‌شود: `clip-subtitled.mp4` و `subs-fa.srt`.

اگر ترجمه آماده نباشد، برنامه بعد از رونویسی می‌ایستد و می‌گوید کدام فایل را
ترجمه کنید؛ بعد با `--skip-transcribe` ادامه می‌دهد.

## ربات تلگرام (اختیاری)

```bash
export TELEGRAM_BOT_TOKEN="..."   # از @BotFather
python -m persian_clip_subtitler.telegram_bot \
    --model models/whisper-small --state-dir state --inbox inbox
```

توکن فقط از متغیر محیطی خوانده می‌شود و هیچ‌وقت داخل کد نمی‌آید.

## مجوز

MIT — فونت وزیرمتن تحت SIL OFL 1.1 است.

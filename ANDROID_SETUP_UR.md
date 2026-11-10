# اینڈرائیڈ فون پر سیٹ اپ (آسان اردو گائیڈ)

**ضروری سچ:** اس گائیڈ کا کوئی بھی قدم ابھی تک اصلی اینڈرائیڈ فون پر آزمایا نہیں گیا۔ Render پر ڈپلائے نہیں ہوا اور APK بنی بھی نہیں۔ نیچے ہر قدم "تیار مگر آزمایا نہیں گیا" ہے۔ جہاں کچھ نہ چلے، وہاں کا سرخ پیغام (error) مجھے بھیجیں۔ **اپنی key یا token کبھی چیٹ میں نہ بھیجیں۔**

دو راستے ہیں۔ **راستہ A (Termux)** میں APK نہیں چاہیے، اور پہلے یہی آزمائیں۔ **راستہ B (APK)** بہتر ہے کیونکہ یہ سسٹم الارم استعمال کرتی ہے، مگر پہلے اسے کلاؤڈ میں بنانا ہوگا۔

---

## حصہ 1: سرور آن لائن کرنا (ایک بار)

آپ کے پاس کمپیوٹر نہیں، اس لیے سب کچھ فون سے۔

1. **GitHub** (github.com) پر مفت اکاؤنٹ بنائیں۔ ایک نیا **private** repository بنائیں، مثلاً `my-ai-agent`۔
2. **مسئلہ:** فون کا براؤزر پورا فولڈر اپلوڈ نہیں کر سکتا، اور اس پروجیکٹ میں کئی فولڈر ہیں۔ اس لیے **Termux** استعمال کریں۔ اسے F-Droid (f-droid.org) سے انسٹال کریں۔ Termux میں یہ لکھیں:
   ```
   pkg update
   pkg install git unzip
   termux-setup-storage
   ```
   پھر ZIP فائل (`personal-agent-v0.3.2.zip`) کو فون کے Downloads میں محفوظ کریں اور یہ لکھیں (`AAPKA-NAAM` کی جگہ اپنا GitHub نام):
   ```
   cd ~
   unzip ~/storage/downloads/personal-agent-v0.3.2.zip
   cd personal-agent
   git init -b main
   git add -A
   git commit -m "first upload"
   git remote add origin https://github.com/AAPKA-NAAM/my-ai-agent.git
   git push -u origin main
   ```
   پاس ورڈ مانگے تو GitHub کا **Personal Access Token** ڈالیں (GitHub کی Settings > Developer settings میں بنتا ہے؛ مینو کے نام بدل سکتے ہیں)۔ **token صرف Termux میں لکھیں، چیٹ میں کبھی نہیں۔**
   اگر یہ مشکل لگے تو کسی ایسے شخص سے کہیں جس کے پاس کمپیوٹر ہو۔ اس کے لیے دو منٹ کافی ہیں۔
3. **render.com** پر مفت اکاؤنٹ بنائیں۔ New > Web Service > اپنا repository چنیں > Runtime **Docker**، Instance **Free**، Health check path `/api/health`۔
4. Render کے **Environment** فارم میں خود لکھیں (چیٹ میں نہیں):
   - `ANTHROPIC_API_KEY` = آپ کی key (console.anthropic.com پر **ماہانہ خرچ کی حد** ضرور لگائیں)
   - `AGENT_TOKEN` = کم از کم 32 حروف کا لمبا خفیہ پاس ورڈ
   - `AGENT_ENABLE_RUN_PYTHON` = `0`
5. Deploy کریں۔ فون کے Chrome میں `https://آپکا-نام.onrender.com` کھولیں اور token ایک بار ڈالیں۔
6. **About** ٹیب میں **Test API key** اور **Test web search** دبائیں اور نتیجہ دیکھیں۔

**Render کے مفت پلان کی حد:** 15 منٹ خاموشی کے بعد سرور سو جاتا ہے، اور **دوبارہ جاگنے پر ڈیٹابیس مٹ جاتا ہے** (ہسٹری، میموری، اور **جوڑے ہوئے فون** سب)۔ پھر فون کو دوبارہ pair کرنا پڑتا ہے۔ فون کا companion یہ پہچان لیتا ہے، رک جاتا ہے اور بتاتا ہے کہ دوبارہ pair کریں۔ اگر فون ہر 30 سیکنڈ بعد سرور کو پوچھتا رہے تو سرور جاگتا رہتا ہے اور مہینے کے مفت گھنٹے (750) تقریباً پورے لگ جاتے ہیں۔

---

## حصہ 2: آواز سے بات کرنا

1. Chrome میں اپنا `https://...onrender.com` کھولیں (آواز کے لیے **HTTPS ضروری** ہے)۔
2. **مائیک** دبائیں۔ پہلی بار Chrome مائیکروفون کی اجازت مانگے گا، **Allow** کریں۔
3. بولیں۔ جو سنائی دیا وہ اسکرین پر "Heard: ..." میں نظر آتا ہے۔ جواب لکھا بھی آتا ہے اور بول کر سنایا بھی جاتا ہے۔
4. **Stop speaking** بولنا روکتا ہے۔ **Cancel** کام روکتا ہے۔ زبان فہرست سے چنیں (English، اردو، ہندی)۔
5. مائیک نہ چلے تو لکھ کر بھیجیں۔ یہ ہمیشہ چلتا ہے۔

یاد رکھیں: Chrome کی آواز پہچان عام طور پر انٹرنیٹ مانگتی ہے، اور اردو/ہندی کی درستگی ہر فون پر یکساں نہیں ہوتی۔ یہ سب صرف نقلی (mock) ٹیسٹوں میں آزمایا گیا، **اصلی فون پر نہیں۔**

---

## راستہ A: Termux companion (APK کے بغیر)

1. F-Droid سے **Termux** اور **Termux:API** دونوں انسٹال کریں (دونوں ایک ہی ذریعے سے، ورنہ آپس میں نہیں چلتے)۔
2. Termux میں:
   ```
   pkg install python termux-api
   ```
   اینڈرائیڈ کی Settings میں Termux کو notifications کی اجازت دیں۔
3. ZIP میں `companion/termux/companion.py` موجود ہے:
   ```
   cd ~/personal-agent/companion/termux
   ```
4. ایجنٹ کے **Phone** ٹیب پر **Create pairing code** دبائیں۔ کوڈ 10 منٹ چلتا ہے اور صرف ایک بار۔
5. Termux میں:
   ```
   python companion.py pair https://aapka-naam.onrender.com ABCD-2345
   python companion.py run
   ```
6. Phone ٹیب میں آپ کا فون "active" دکھنا چاہیے۔
7. ایجنٹ سے کہیں: "مجھے ایک notification بھیجو" یا "دس منٹ بعد چائے کی یاد دلاؤ"۔

**حدیں:** reminder صرف تب بجتا ہے جب Termux چل رہا ہو (`termux-wake-lock` چلائیں اور بیٹری سیور سے Termux کو مستثنیٰ کریں)۔ یہ سسٹم الارم نہیں ہے۔
**بند کرنا:** `python companion.py unpair` یا Phone ٹیب میں **Disconnect this phone**۔

---

## راستہ B: اصلی APK (GitHub کلاؤڈ میں بنائیں)

1. اپنے repository میں **Actions** ٹیب > **Build Android companion (debug APK)** > **Run workflow**۔
2. کامیاب ہو تو رن کے اندر **agent-companion-debug-apk** artifact ڈاؤن لوڈ کریں (یہ zip ہوتی ہے، کھول کر `.apk` نکالیں)۔
3. APK انسٹال کرنے کے لیے اینڈرائیڈ "unknown apps" کی اجازت مانگے گا۔ صرف اپنی بنائی ہوئی فائل کے لیے دیں۔
4. ایپ کھولیں: سرور ایڈریس اور pairing code ڈالیں > **Pair this phone**۔
5. **Allow notifications** اور (reminders کے لیے) **Allow exact reminders** دبائیں۔

**یہ workflow اور ایپ کا کوڈ ابھی تک کبھی compile نہیں ہوا۔** پہلی بار build فیل ہو سکتی ہے (ورژن یا syntax کی غلطی)۔ سرخ error کا متن مجھے بھیجیں، میں ٹھیک کر دوں گا۔
ایپ بند ہو تو نئے commands زیادہ سے زیادہ ہر ~15 منٹ میں آتے ہیں (اینڈرائیڈ کی حد)۔ ایپ کھلی ہو تو ہر 30 سیکنڈ۔

---

## ایپ کیا کر سکتی ہے اور کیا نہیں
- **کر سکتی ہے:** notification دکھانا، reminder لگانا/ہٹانا، ایک https لنک دکھانا (جو آپ tap کریں تو کھلتا ہے؛ لنک دکھانے سے پہلے آپ سے اجازت لی جاتی ہے)۔
- **نہیں کر سکتی:** SMS، کال، contacts، لوکیشن، مائیکروفون، دوسری ایپس چلانا، shell۔ یہ کوڈ میں ہیں ہی نہیں۔
- ایجنٹ "ہو گیا" تب کہتا ہے جب فون خود کامیابی بتائے۔ ورنہ "قطار میں ہے"۔

## آپ کو خود کیا کرنا ہے (خلاصہ)
1. GitHub اور Render اکاؤنٹ، ANTHROPIC key (خرچ کی حد کے ساتھ)، لمبا AGENT_TOKEN۔
2. حصہ 1 کے قدم، پھر About ٹیب کے دونوں ٹیسٹ۔
3. پہلے راستہ A آزمائیں۔ پھر چاہیں تو راستہ B۔
4. جو نہ چلے اس کا error متن بھیجیں (key یا token کبھی نہیں)۔

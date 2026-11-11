# Android par Personal AI Agent — Free local setup (Termux)

**Payment card ya Render ki zaroorat nahi.** Is setup mein aapka Android phone hi server hoga. Phone band, Termux force-stop, ya internet band ho to remote access/AI tasks ruk sakte hain. Yeh guide abhi real phone par verify nahi hui.

**Security:** Gemini API key ya AGENT_TOKEN ChatGPT/GitHub mein share na karein. Key sirf apne phone ke private `.env` file mein rakhein. Is project ko sirf apne phone par local access ke liye chalayein.

## 1) Termux install karein
1. F-Droid ki official website se Termux install karein: https://f-droid.org/packages/com.termux/
2. Termux khol kar commands ek ek karke chalayein:

```bash
pkg update -y && pkg upgrade -y
pkg install -y git python nano
```

## 2) Project download karein
```bash
cd ~
git clone --branch gemini-free-tier https://github.com/iqrarkhilji001-ui/Khilji-agent-.git personal-agent
cd ~/personal-agent
```

Agar GitHub branch ka naam ya repo access error de, error ka text bhejein — API key ya token nahin.

## 3) Python packages install karein
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 4) Gemini API key set karein
Google AI Studio se apni Gemini API key khud hasil karein: https://aistudio.google.com/app/apikey

Phir Termux mein:
```bash
cd ~/personal-agent
cp .env.example .env
nano .env
```

File mein `GEMINI_API_KEY=` ke baad apni key likhein. Misal ke taur par:
```
GEMINI_API_KEY=YOUR_PRIVATE_KEY_HERE
```
Baaki settings waise hi rehne dein. Save: **Ctrl+O**, Enter; exit: **Ctrl+X**. Termux mein Ctrl key keyboard toolbar se mil sakti hai.

**Key ko GitHub par commit/upload na karein.** `.env` ko project ki .gitignore mein ignore hona chahiye.

## 5) Pehle configuration check karein
```bash
python run.py --check
```
Yeh Gemini API ko chhoti real request bhejta hai aur web search check karta hai. API check fail ho to error ka text bhejein — key khud na bhejein.

## 6) Agent start karein
```bash
python run.py
```
Pehli dafa terminal mein access token generate ho sakta hai. Isay private rakhein. App kholne ke liye isi phone ke Chrome mein:
```
http://127.0.0.1:8080
```
Token app mein enter karein. Termux ko band na karein; server chalne ke liye session active rehna chahiye.

## 7) Rozana use / band karna
- Server chal raha ho to Chrome mein `http://127.0.0.1:8080` kholein.
- Server band karna ho to Termux mein **Ctrl+C**.
- Dobara chalana ho:
  ```bash
  cd ~/personal-agent
  python run.py
  ```
- Android Battery settings mein Termux ke liye battery restriction hata dein. Android phir bhi background process ko rok sakta hai.
- Phone restart ke baad Termux khol kar server dobara start karein.

## 8) Project ki built-in limits
- Trial: pehli server startup se 30 din.
- AI model runtime cap: kul 10 minute har Pakistan-time din mein; midnight par reset.
- Yeh app ke apne limits hain; Gemini ki free-tier availability/model limits alag hain aur Google badal sakta hai.
- Local SQLite data phone ke project/data folder mein rahega. Folder delete ya app data clear karne se history/trial record mit sakta hai.
- Phone par local setup remote public website nahin banata. Bahar se access ke liye secure hosting/network setup alag chahiye; is guide mein port internet par expose nahi kiya gaya.

## Troubleshooting
- `ModuleNotFoundError`: `pip install -r requirements.txt` dobara chalayein.
- `GEMINI_API_KEY is not set`: `.env` mein key ki line check karein.
- `Address already in use`: pehle chal raha server band karein (Ctrl+C), phir dobara start karein.
- Kisi bhi error ka text/screenshot bhej sakte hain, lekin API key, access token, ya private credentials chhupa kar.

**Status:** GitHub branch se guide update hui hai. Termux install, phone par test, aur actual Gemini API run abhi verify nahi hue.

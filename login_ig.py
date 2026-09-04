# login_ig.py  v7 (final) -- full auto IG login incl. 2FA, via a polling state machine.
# Reads creds from CSV, types char-by-char, dismisses Google + IG popups, generates its own 2FA code.
import os
from farm_root import ROOT
import subprocess, time, re, csv, hmac, hashlib, base64, struct, xml.etree.ElementTree as ET

SERIAL  = "988a5745584f554c4230"
IG_PKG  = "com.instagram.androif"                       # the IG clone to log into
CSVPATH = os.path.join(ROOT, 'alive_accounts_credentials.csv')
ROW     = 1                                             # which account row (0 = first)

A=["adb","-s",SERIAL]
def sh(*a, cap=False):
    r=subprocess.run(A+["shell",*a], capture_output=True, text=True, errors="ignore"); return r.stdout if cap else None
def dump():
    sh("uiautomator","dump","/sdcard/ui.xml"); x=sh("cat","/sdcard/ui.xml",cap=True) or ""
    i=x.find("<?xml"); x=x[i:] if i>=0 else x
    try: return [n.attrib for n in ET.fromstring(x).iter("node")]
    except Exception: return []
def center(b):
    m=re.findall(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b or "")
    if not m: return None
    x1,y1,x2,y2=map(int,m[0]); return (x1+x2)//2,(y1+y2)//2
def edits(ns, pw=None):
    out=[a for a in ns if a.get("class")=="android.widget.EditText"]
    if pw is True:  out=[a for a in out if a.get("password")=="true"]
    if pw is False: out=[a for a in out if a.get("password")!="true"]
    return out
def find(ns, text=None, desc=None, c=False):
    for a in ns:
        if text is not None and not ((text in a.get("text","")) if c else a.get("text","")==text): continue
        if desc is not None and not ((desc in a.get("content-desc","")) if c else a.get("content-desc","")==desc): continue
        return a
    return None
def tapn(n,label):
    if not n: return False
    p=center(n.get("bounds")); print(f"[tap] {label} -> {p}"); sh("input","tap",str(p[0]),str(p[1])); return True
def typ(s):
    for ch in s: sh("input","text","%s" if ch==" " else ch); time.sleep(0.12)
def totp(secret):
    key=base64.b32decode(secret+"="*((8-len(secret)%8)%8)); c=int(time.time())//30
    h=hmac.new(key,struct.pack(">Q",c),hashlib.sha1).digest(); o=h[19]&15
    return f"{(struct.unpack('>I',h[o:o+4])[0]&0x7fffffff)%1000000:06d}"
def set_field(want_pw, value, label):
    for attempt in range(3):
        f=edits(dump(), pw=want_pw)
        if not f: return False
        tapn(f[0],label); time.sleep(1.2)
        sh("input","keyevent","123")
        for _ in range(25): sh("input","keyevent","67")
        time.sleep(.3); typ(value); time.sleep(.8)
        if want_pw: print(f"[ok] {label} entered"); return True
        g=edits(dump(),pw=False); got=(g[0].get("text","") if g else "")
        if got.strip()==value: print(f"[ok] {label} = {got!r}"); return True
        print(f"[retry {attempt+1}] {label} shows {got!r}")
    return False

acc=list(csv.DictReader(open(CSVPATH,encoding="utf-8")))[ROW]
user,pwd,sec=acc["username"].strip(),acc["password"].strip(),acc["tfa_secret"].strip()
print(f"[acct] {user} on {IG_PKG}")

did_login=did_2fa=False
for step in range(40):
    ns=dump(); page=" ".join(a.get("text","") for a in ns).lower()
    pkgs=set(a.get("package","") for a in ns if a.get("package"))

    # Google "Save password?" popup -> Never (do NOT relaunch IG)
    if "save password" in page or "use saved passwords" in page:
        print("[dismiss] Google save-password -> Never")
        tapn(find(ns,text="Never") or find(ns,text="No thanks") or find(ns,text="Not now"),"Never"); time.sleep(2); continue

    # make sure we're actually inside Instagram
    if IG_PKG not in pkgs:
        print(f"[wait] not in IG (saw: {next(iter(pkgs),'?')}), opening Instagram")
        sh("monkey","-p",IG_PKG,"-c","android.intent.category.LAUNCHER","1"); time.sleep(6); continue

    # logged in? (real IG feed markers)
    if (find(ns,desc="Home",c=True) or find(ns,desc="Reels",c=True) or find(ns,desc="Direct",c=True)
            or find(ns,desc="Camera",c=True) or find(ns,text="Your story",c=True) or find(ns,text="Suggested for you",c=True)):
        print("[SUCCESS] logged in, on IG feed"); break

    # 2FA from authenticator
    if any(k in page for k in ("authentication app","6-digit","two-factor","security code")):
        if not did_2fa:
            code=totp(sec); print(f"[2fa] entering {code}")
            f=edits(dump())
            if f: tapn(f[0],"2fa field"); time.sleep(.6); typ(code); time.sleep(.8)
            tapn(find(dump(),text="Continue") or find(dump(),text="Confirm") or find(dump(),text="Next"),"continue")
            did_2fa=True; time.sleep(7)
        else: time.sleep(2)
        continue

    # email/SMS code we can't satisfy
    if "we sent" in page or "enter the code we" in page:
        print("[STOP] needs an email/SMS code we don't have."); break

    # dismiss IG / system popups
    d=(find(ns,text="Not now") or find(ns,text="Not Now") or find(ns,text="Skip")
       or find(ns,text="Got it") or find(ns,text="OK"))
    if d: tapn(d,"dismiss"); time.sleep(2); continue

    # login form
    if len(edits(ns))>=2 and (find(ns,text="Log in") or find(ns,desc="Log in")):
        if did_login: print("[STOP] back at login - creds rejected."); break
        if not set_field(False,user,"username"): break
        if not set_field(True, pwd,"password"): break
        b=find(dump(),text="Log in") or find(dump(),desc="Log in")
        if b: tapn(b,"Log in"); did_login=True; time.sleep(7)
        else: print("[info] Log in not visible yet"); time.sleep(2)
        continue

    time.sleep(2)
else:
    print("[?] gave up after 40 polls - screenshot the phone")

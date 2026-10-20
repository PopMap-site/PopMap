#!/usr/bin/env python3
"""POPMAP — génère la voix radio (Pierre, effet poste années 40) de chaque fiche.
Usage : python3 tools/radio_build.py   (depuis la racine du dépôt)
Ne régénère que les fiches dont le texte a changé ; écrit audio/*.mp3 et audio/index.json.
Dépendances : pip install sherpa-onnx numpy scipy ; ffmpeg ; la voix est téléchargée au premier lancement."""
import sherpa_onnx, numpy as np, subprocess, sys, os, re, tempfile
from scipy.io import wavfile
from scipy.signal import butter, lfilter
D=os.environ.get('POPMAP_VOICE_DIR',os.path.expanduser('~/.cache/popmap-voice'))
VOICES={'tom':('vits-piper-fr_FR-tom-medium','fr_FR-tom-medium.onnx',0),
        'gilles':('vits-piper-fr_FR-gilles-low','fr_FR-gilles-low.onnx',0),
        'pierre':('vits-piper-fr_FR-upmc-medium','fr_FR-upmc-medium.onnx',1)}
_tts={}
def tts(name):
    if name not in _tts:
        d,m,_=VOICES[name];d=os.path.join(D,d)
        cfg=sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(vits=sherpa_onnx.OfflineTtsVitsModelConfig(model=f'{d}/{m}',tokens=f'{d}/tokens.txt',data_dir=f'{d}/espeak-ng-data'),num_threads=4))
        _tts[name]=sherpa_onnx.OfflineTts(cfg)
    return _tts[name]
SR=22050
def bp(x,lo,hi,sr=SR,o=4):
    b,a=butter(o,[lo/(sr/2),hi/(sr/2)],'band');return lfilter(b,a,x)
def drum(dur):  # timbale grave
    t=np.arange(int(SR*dur))/SR
    f=np.exp(-t*7)*(np.sin(2*np.pi*72*t)+.5*np.sin(2*np.pi*108*t)+.25*np.sin(2*np.pi*146*t))
    f[:200]+=np.random.randn(200)*np.linspace(.6,0,200)
    return f*.7
def sil(s): return np.zeros(int(SR*s))
def intro():  # motif « V » : trois brèves, une longue (Beethoven / morse V)
    return np.concatenate([drum(.28),drum(.28),drum(.28),drum(1.3),sil(.35)])
def speak(name,text,speed=.9):
    _,_,sid=VOICES[name];a=tts(name).generate(text,sid=sid,speed=speed)
    x=np.array(a.samples,dtype=np.float32)
    if a.sample_rate!=SR:
        from scipy.signal import resample_poly;x=resample_poly(x,SR,a.sample_rate)
    return x
def age(x):
    x=x/ (np.abs(x).max()+1e-9)
    # bande téléphone/AM + saturation douce
    y=bp(x,320,3100); y=np.tanh(y*2.6)/np.tanh(2.6)
    # pleurage (wow) léger
    n=len(y);t=np.arange(n)/SR;idx=np.clip(t*SR+ (np.sin(2*np.pi*.55*t)*18),0,n-1)
    y=np.interp(idx,np.arange(n),y)
    # souffle + crépitements
    hiss=bp(np.random.randn(n),900,3600)*.035
    cr=np.zeros(n);k=np.random.rand(n)<0.0012;cr[k]=np.random.randn(k.sum())*.55;cr=bp(cr,600,4500,o=2)
    hum=.012*np.sin(2*np.pi*50*t)
    y=y*.82+hiss+cr+hum
    return y/np.abs(y).max()*.9
VF=("highpass=f=260:p=2,lowpass=f=3600:p=2,"
    "equalizer=f=1250:t=q:w=1.1:g=7,equalizer=f=2600:t=q:w=1.4:g=2,equalizer=f=180:t=q:w=1:g=-6,"
    "acompressor=threshold=-24dB:ratio=5:attack=4:release=90:makeup=4,"
    "asoftclip=type=atan:param=1.1,"
    "aecho=0.85:0.45:28|57:0.22|0.10,"
    "highpass=f=280,lowpass=f=3500")
def voice_fx(x):
    fd,a=tempfile.mkstemp(suffix='.wav');os.close(fd);b=a+'.o.wav'
    wavfile.write(a,SR,(x/np.abs(x).max()*.8*32767).astype(np.int16))
    subprocess.run(['ffmpeg','-y','-loglevel','error','-i',a,'-af',VF,'-ar',str(SR),b],check=True)
    _,y=wavfile.read(b);os.remove(a);os.remove(b);return y.astype(np.float32)/32767
def bed(n,voice):
    t=np.arange(n)/SR
    env=np.convolve(np.abs(voice),np.ones(2000)/2000,'same');env=np.clip(env/ (env.max()+1e-9)*3,0,1)
    duck=1-.55*env
    hiss=bp(np.random.randn(n),900,3600)*.03
    cr=np.zeros(n);k=np.random.rand(n)<0.001;cr[k]=np.random.randn(k.sum())*.5;cr=bp(cr,600,4500,o=2)
    hum=.01*np.sin(2*np.pi*50*t)
    return (hiss+cr)*duck+hum
def make(name,text,out,speed=.86):
    parts=[p.strip() for p in re.split(r'(?<=[.!?;:])\s+',"Ici POPMAP. "+text) if p.strip()]
    segs=[]
    for i,p in enumerate(parts):
        segs.append(speak(name,p,speed));segs.append(sil(.55 if i==0 else .32))
    v=np.concatenate(segs);v=voice_fx(v)
    n=len(v);tt=np.arange(n)/SR;idx=np.clip(tt*SR+np.sin(2*np.pi*.5*tt)*10,0,n-1);v=np.interp(idx,np.arange(n),v)
    dr=bp(intro(),60,3200,o=2);dr=np.tanh(dr*1.8)
    x=np.concatenate([sil(.15),dr/np.abs(dr).max()*.75,v/np.abs(v).max()*.85,sil(.4)])
    voice_only=np.concatenate([sil(.15),np.zeros(len(dr)),v,sil(.4)])
    y=x+bed(len(x),voice_only);y=y/np.abs(y).max()*.9
    fd,w=tempfile.mkstemp(suffix='.wav');os.close(fd)
    wavfile.write(w,SR,(y*32767).astype(np.int16))
    subprocess.run(['ffmpeg','-y','-loglevel','error','-i',w,'-ac','1','-ar','22050','-c:a','libmp3lame','-b:a','40k',out],check=True);os.remove(w)
def clean(s): return re.sub(r'\s+',' ',re.sub(r'<[^>]+>','',s or '')).strip()

import json, hashlib, glob
URL='https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-fr_FR-upmc-medium.tar.bz2'
def ensure_voice():
    d=os.path.join(D,'vits-piper-fr_FR-upmc-medium')
    if os.path.exists(os.path.join(d,'fr_FR-upmc-medium.onnx')): return
    os.makedirs(D,exist_ok=True);t=os.path.join(D,'v.tar.bz2')
    subprocess.run(['curl','-sSL','-o',t,URL],check=True);subprocess.run(['tar','xjf',t,'-C',D],check=True);os.remove(t)
def say(s):
    s=clean(s).replace(' ',' ').replace(' ',' ')
    s=re.sub(r'(?<=\d) (?=\d{3}\b)','',s)
    s=re.sub(r'(\d+) ?h ?(\d{2})\b',r'\1 heures \2',s);s=re.sub(r'(\d+) ?h\b',r'\1 heures',s)
    s=re.sub(r'(\d+)-(octobre|novembre|décembre|janvier|septembre)',r'\1 \2',s)
    s=s.replace('–',' à ').replace('—',', ').replace('%',' pour cent').replace(' km',' kilomètres').replace('≈','environ')
    s=s.replace('(',', ').replace(')',', ').replace('«','').replace('»','').replace('"','')
    return re.sub(r'\s+',' ',s).strip()
def items(E):
    for e in E:
        yield e['id'], e['place']+'|'+e.get('title','')+'|'+e.get('summary',''), say(f"{e['place']}. {e.get('title','')}. {e.get('summary','')}")
        for i,s in enumerate(e.get('spots') or []):
            yield e['id']+str(i), e['place']+'|'+s['n']+'|'+s.get('d',''), say(f"{e['place']}, {s['n']}. {s.get('d','')}")
def main():
    E=json.load(open('data/events.json'));os.makedirs('audio',exist_ok=True)
    try: idx=json.load(open('audio/index.json'))
    except Exception: idx={}
    new={};todo=[]
    for k,src,txt in items(E):
        h=hashlib.sha1(src.encode()).hexdigest()[:12]
        new[k]={'h':h,'src':src}
        if not os.path.exists(f'audio/{h}.mp3'): todo.append((h,txt))
    if todo: ensure_voice()
    done=set()
    for n,(h,txt) in enumerate(todo,1):
        if h in done: continue
        make('pierre',txt,f'audio/{h}.mp3');done.add(h);print(f'{n}/{len(todo)} {h}',flush=True)
    keep={v['h'] for v in new.values()}
    for f in glob.glob('audio/*.mp3'):
        if os.path.basename(f)[:-4] not in keep: os.remove(f)
    json.dump(new,open('audio/index.json','w'),ensure_ascii=False,separators=(',',':'))
    print(f'{len(todo)} générés, {len(new)} fiches avec voix')
if __name__=='__main__': main()

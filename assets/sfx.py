import numpy as np, wave
SR=48000; D=2.0; N=int(SR*D); t=np.arange(N)/SR
rng=np.random.default_rng(7)
def lp(sig,cut):
    a=np.exp(-2*np.pi*np.asarray(cut)/SR); y=np.zeros_like(sig); acc=0.0
    a=np.broadcast_to(a,sig.shape)
    for i in range(len(sig)): acc=(1-a[i])*sig[i]+a[i]*acc; y[i]=acc
    return y
def hp(sig,cut): return sig-lp(sig,cut)
def env(s,att,dec,length=None):
    e=np.zeros(N); i=t>=s; tt=t[i]-s
    e[i]=np.minimum(tt/max(att,1e-4),1)*np.exp(-np.maximum(tt-att,0)/dec)
    if length: e[t>s+length]=0
    return e
def osc(freq,shape='sine'):
    ph=2*np.pi*np.cumsum(np.broadcast_to(freq,(N,)))/SR
    if shape=='saw': return 2*((ph/(2*np.pi))%1)-1
    if shape=='square': return np.sign(np.sin(ph))
    return np.sin(ph)
def reverb(sig,decay=1.2,mix=.3):
    L=int(SR*decay); ir=rng.standard_normal(L)*np.exp(-np.arange(L)/SR/(decay/5)); ir=lp(ir,6000); ir/=np.sqrt(np.sum(ir**2))
    wet=np.convolve(sig,ir)[:N]; return sig*(1-mix)+wet*mix*2.5
def save(name,L,Rr):
    m=max(np.max(np.abs(L)),np.max(np.abs(Rr))); L=L/m*.89; Rr=Rr/m*.89
    f=np.clip((D-t)/.18,0,1); fi=np.clip(t/.01,0,1); L*=f*fi; Rr*=f*fi
    s=(np.stack([L,Rr],1)*32767).astype(np.int16); w=wave.open(name,'wb'); w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(s.tobytes()); w.close()

# ---------- FIVE STAR: sirene (yelp) passando + estrelas + impacto ----------
yelp=750+520*(0.5-0.5*np.cos(2*np.pi*3.2*t))           # sobe e desce rapido
siren=osc(yelp,'square')*.5+osc(yelp*2)*.25+osc(yelp)*.6
siren=lp(siren,2600)
dist=np.clip(0.35+0.65*np.sin(np.pi*np.clip(t/1.9,0,1)),0,1)  # aproxima e afasta
siren*=dist*.32
panL=0.5+0.4*np.cos(np.pi*t/2); panR=1-panL               # passa da esquerda para a direita
# chiado de radio
radio=hp(rng.standard_normal(N),1800)*(.03+.03*(np.sin(t*37)>0.6))*np.clip(1-t/0.6,0,1)
stars=np.zeros(N)
for i in range(5):
    s=.12+i*.11; f0=880*(2**(i*2/12))
    stars+=(osc(f0)+.5*osc(f0*2.01)+.3*osc(f0*3.03))*env(s,.002,.09)*.5
    stars+=hp(rng.standard_normal(N),5000)*env(s,.001,.015)*.4
riser=hp(rng.standard_normal(N),800)*np.clip((t-.35)/.4,0,1)**3*(t<.75)
riser=lp(riser,1500+9000*np.clip((t-.35)/.4,0,1))*.6
sub=osc(np.maximum(38,95*np.exp(-(t-.74)*6)))*env(.74,.003,.45)*1.4
hit=lp(rng.standard_normal(N),3500)*env(.74,.001,.08)*1.0+osc(140)*env(.74,.002,.12)*.6
ring=(osc(523.25)+osc(659.25)*.6+osc(784)*.5)*env(.95,.01,.5)*.12          # brilho do CLIPS
mono=stars+riser+sub+hit+ring+radio
mono=reverb(mono,1.0,.22); sirenw=reverb(siren,1.6,.45)
save('five2.wav',mono+sirenw*panL*2,mono+sirenw*panR*2)

# ---------- RESPECT+: arpejo synthwave + whoosh + impacto + brilho ----------
bpm=150; step=60/bpm/4
notes=[57,60,64,69,72,69,64,60]   # La menor
arp=np.zeros(N)
for k in range(int(.75/step)+1):
    s=k*step; f=440*2**((notes[k%8]-69)/12)
    e=env(s,.003,.09,step*1.1)
    arp+=(osc(f,'saw')*.6+osc(f*1.005,'saw')*.4)*e
arp=lp(arp,600+3500*np.clip(t/.75,0,1))*.35*(t<.85)
pad=sum(osc(440*2**((m-69)/12),'saw') for m in (45,52,57,60,64))*.12
pad=lp(pad,900+1500*np.clip((t-.75)/.3,0,1))*np.clip(t/.4,0,1)
whoosh=lp(hp(rng.standard_normal(N),300),800+8000*np.clip((t-.3)/.45,0,1))*np.clip((t-.3)/.45,0,1)**2*(t<.76)*.7
chord=sum(osc(440*2**((m-69)/12),'saw') for m in (57,60,64,67,72))
chord=lp(chord,5000)*env(.75,.003,.55)*.35
sub=osc(np.maximum(40,110*np.exp(-(t-.75)*7)))*env(.75,.003,.35)*1.3
snap=hp(rng.standard_normal(N),2000)*env(.75,.001,.06)*.7
bells=np.zeros(N)
for k,(s,m) in enumerate([(.86,84),(.94,88),(1.02,91),(1.1,96)]):
    f=440*2**((m-69)/12); bells+=(osc(f)+.4*osc(f*2.76))*env(s,.002,.25)*.18
mono=arp+pad+whoosh+chord+sub+snap+bells
wetL=reverb(mono,1.4,.35)
save('respect2.wav',wetL+arp*.15,wetL-arp*.15)

"""Indicators and the 10-check trend score (from the starter kit, unchanged; the only trend score used)."""
import pandas as pd,numpy as np
def indicators(d):
    c,h,l,o=d.Close.astype(float),d.High.astype(float),d.Low.astype(float),d.Open.astype(float)
    flat=(o==c)&(h==l); h=h.mask(flat,c); l=l.mask(flat,c)
    I=pd.DataFrame(index=d.index); I["close"]=c
    for n in [20,50,100,200]: I[f"sma{n}"]=c.rolling(n).mean()
    ema=lambda s,n:s.ewm(span=n,adjust=False).mean()
    I["macd"]=ema(c,12)-ema(c,26); I["macds"]=ema(I.macd,9); I["macdh"]=I.macd-I.macds
    dl=c.diff(); up=dl.clip(lower=0); dn=-dl.clip(upper=0)
    I["rsi14"]=100-100/(1+up.ewm(alpha=1/14,adjust=False).mean()/dn.ewm(alpha=1/14,adjust=False).mean())
    ll,hh=l.rolling(14).min(),h.rolling(14).max(); I["stk"]=100*(c-ll)/(hh-ll)
    m20,s20=c.rolling(20).mean(),c.rolling(20).std(); I["pctb"]=(c-(m20-2*s20))/(4*s20)
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1); atr=tr.ewm(alpha=1/14,adjust=False).mean(); I["atrp"]=atr/c
    upm=h.diff(); dnm=-l.diff(); pdm=pd.Series(np.where((upm>dnm)&(upm>0),upm,0),index=c.index); ndm=pd.Series(np.where((dnm>upm)&(dnm>0),dnm,0),index=c.index)
    I["pdi"]=100*pdm.ewm(alpha=1/14,adjust=False).mean()/atr; I["ndi"]=100*ndm.ewm(alpha=1/14,adjust=False).mean()/atr
    I["adx"]=(100*(I.pdi-I.ndi).abs()/(I.pdi+I.ndi)).ewm(alpha=1/14,adjust=False).mean()
    a10=tr.ewm(alpha=1/10,adjust=False).mean(); hl2=(h+l)/2; ub=(hl2+3*a10).values; lb=(hl2-3*a10).values; cv=c.values
    fu,fl,st=ub.copy(),lb.copy(),np.ones(len(c))
    for i in range(1,len(c)):
        fu[i]=ub[i] if (ub[i]<fu[i-1] or cv[i-1]>fu[i-1]) else fu[i-1]
        fl[i]=lb[i] if (lb[i]>fl[i-1] or cv[i-1]<fl[i-1]) else fl[i-1]
        st[i]=1 if cv[i]>fu[i-1] else (-1 if cv[i]<fl[i-1] else st[i-1])
    I["st_dir"]=st; I["st_line"]=np.where(st>0,fl,fu)
    I["hi52"],I["lo52"]=h.rolling(252).max(),l.rolling(252).min()
    I["vol20"]=c.pct_change().rolling(20).std()*np.sqrt(252)
    return I
def score(x):
    """10 trend votes -> bullish count"""
    v=[x.close>x.sma50,x.close>x.sma200,x.sma50>x.sma200,x.macd>x.macds,x.rsi14>50,x.pdi>x.ndi,x.st_dir>0,x.close>x.sma20,x.macd>0,x.close>x.sma100]
    return int(sum(bool(a) for a in v))


def state_label(s):
    return "Strong uptrend" if s >= 8 else "Uptrend" if s >= 6 else "Mixed" if s >= 4 else "Downtrend" if s >= 2 else "Strong downtrend"


def rsi_label(r):
    return "Stretched up" if r > 70 else "Stretched down" if r < 30 else "Normal"


def pivots(h, l, c):
    """Classic daily pivots from the latest bar (for the next session)."""
    p = (h + l + c) / 3
    return dict(P=p, R1=2 * p - l, S1=2 * p - h, R2=p + (h - l), S2=p - (h - l))


def score_series(I):
    """Trend score for every row (NaN while long averages are still warming up)."""
    import numpy as np
    ok = I[["sma200", "macds", "adx"]].notna().all(axis=1)
    v = ((I.close > I.sma50).astype(int) + (I.close > I.sma200) + (I.sma50 > I.sma200) + (I.macd > I.macds)
         + (I.rsi14 > 50) + (I.pdi > I.ndi) + (I.st_dir > 0) + (I.close > I.sma20) + (I.macd > 0) + (I.close > I.sma100))
    return v.where(ok, np.nan)

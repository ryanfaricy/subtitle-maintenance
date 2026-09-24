"""Conservative global affine timing fit. No piecewise edits or threshold relaxation."""
import re
import statistics
from .validation import phrase_evidence

SAMPLES=(.05,.15,.5,.85,.95)

def combined_episode(name):
    return bool(re.search(r'S\d+E\d+(?:\s*-?\s*E\d+|\s*-\s*\d+)',name,re.I))

def fit(cues,words,duration):
    # Independent sample regions cannot influence the fit, even via full-ASR words.
    windows=[(round(duration*f)-15,round(duration*f)+55) for f in SAMPLES]
    evidence=phrase_evidence(cues,words)
    anchors=[e for e in evidence if not any(e['audio_start']<=hi and e['audio_end']>=lo for lo,hi in windows)]
    if len(anchors)<100:raise ValueError('Drift fit needs 100 matched cues outside verification windows')
    points=[((e['start']+e['end'])/2,(e['audio_start']+e['audio_end'])/2) for e in anchors]
    points.sort()
    if points[-1][1]-points[0][1]<duration*.65:raise ValueError('Drift evidence has insufficient timeline span')
    # Bounded Theil-Sen-style estimate reduces the influence of ASR outliers.
    sampled=points[::max(1,len(points)//180)]
    slopes=[(y2-y1)/(x2-x1) for i,(x1,y1) in enumerate(sampled) for x2,y2 in sampled[i+1:] if x2-x1>duration*.25]
    if not slopes:raise ValueError('Insufficient separated drift anchors')
    scale=statistics.median(slopes)
    offset=statistics.median(y-scale*x for x,y in points)
    if not .95<=scale<=1.05 or abs(scale-1)<.001:raise ValueError('Drift scale outside trial bounds or indistinguishable from offset')
    if abs(offset)>60:raise ValueError('Drift intercept exceeds 60 seconds')
    residuals=sorted(abs(y-(scale*x+offset)) for x,y in points)
    if statistics.median(residuals)>.5 or residuals[int(.9*(len(residuals)-1))]>1.5:
        raise ValueError('Inconsistent drift fit; possible edit differences or unreliable anchors')
    for i in range(5):
        residual=[y-(scale*x+offset) for x,y in points if duration*i/5<=y<duration*(i+1)/5]
        if len(residual)<10 or abs(statistics.median(residual))>.75:
            raise ValueError('Drift fit fails regional consistency')
    return dict(scale=scale,offset=offset,anchors=len(points),median_residual=statistics.median(residuals),
                p90_residual=residuals[int(.9*(len(residuals)-1))])

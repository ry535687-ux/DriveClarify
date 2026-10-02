"""仅配对次级分析；不实现官方Driving Score，且不遮蔽stdlib statistics。"""
import numpy as np
from scipy.stats import binomtest

def continuous(a0,a1,seed,resamples=100000):
    left=np.asarray(a0,dtype=float);right=np.asarray(a1,dtype=float)
    if left.shape!=right.shape or left.ndim!=1 or len(left)==0 or not np.isfinite(left).all() or not np.isfinite(right).all():
        raise ValueError('COMPLETE_FINITE_PAIRED_VALUES_REQUIRED')
    delta=right-left;rng=np.random.Generator(np.random.PCG64(seed));means=np.empty(resamples)
    for start in range(0,resamples,1000):
        count=min(1000,resamples-start)
        means[start:start+count]=delta[rng.integers(0,len(delta),size=(count,len(delta)))].mean(axis=1)
    return {'n':len(left),'A0_mean':float(left.mean()),'A0_median':float(np.median(left)),
      'A1_mean':float(right.mean()),'A1_median':float(np.median(right)),
      'paired_mean_difference':float(delta.mean()),'paired_median_difference':float(np.median(delta)),
      'paired_bootstrap_95_CI':np.percentile(means,[2.5,97.5]).tolist(),'resamples':resamples,'RNG_seed':seed,
      'method':'paired route percentile bootstrap, A1 minus A0'}

def binary(a0,a1):
    if len(a0)!=len(a1) or not a0:raise ValueError('COMPLETE_NONEMPTY_PAIRS_REQUIRED')
    table={'both_true':0,'A0_true_A1_false':0,'A0_false_A1_true':0,'both_false':0}
    for a,b in zip(a0,a1):
        key='both_true' if a and b else 'A0_true_A1_false' if a else 'A0_false_A1_true' if b else 'both_false'
        table[key]+=1
    b=table['A0_true_A1_false'];c=table['A0_false_A1_true']
    return {'n':len(a0),'A0_count':int(sum(a0)),'A1_count':int(sum(a1)),'discordance_table':table,
      'exact_McNemar_p':float(binomtest(b,b+c,0.5).pvalue) if b+c else 1.0,
      'delta_percentage_points':100.0*(sum(a1)-sum(a0))/len(a0)}

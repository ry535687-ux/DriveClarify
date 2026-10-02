"""候选统计实现；只有 Phase B receipt 才能将其作为正式冻结方案。"""
import math
import numpy as np
from scipy.stats import binomtest,beta


def paired_binary(a0,a1):
    assert len(a0)==len(a1) and all(x in (0,1) for x in [*a0,*a1])
    n=len(a0)
    if not n:return {'n':0,'risk_difference':None,'ci95':None,'exact_mcnemar_p':None}
    cells={str(i)+str(j):sum(x==i and y==j for x,y in zip(a0,a1)) for i in (0,1) for j in (0,1)}
    benefit,harm=cells['01'],cells['10'];discord=benefit+harm
    def cp(k):
        # 两个边际区间各 97.5%，Bonferroni 联合覆盖至少95%。
        return (0. if k==0 else float(beta.ppf(.0125,k,n-k+1)),1. if k==n else float(beta.ppf(.9875,k+1,n-k)))
    lb,ub=cp(benefit);lh,uh=cp(harm)
    return {'n':n,'a0_successes':sum(a0),'a1_successes':sum(a1),'table_rows_A0_columns_A1':cells,'benefit_pairs':benefit,'harm_pairs':harm,'risk_difference':(benefit-harm)/n,'ci95':[max(-1,lb-uh),min(1,ub-lh)],'ci_method':'PAIRED_DISCORDANCE_CLOPPER_PEARSON_BONFERRONI_CONSERVATIVE_95','exact_mcnemar_p':1. if not discord else float(binomtest(benefit,discord,.5,alternative='two-sided').pvalue)}


def paired_continuous(a0,a1,analysis_seed=721936,replicates=50000):
    assert len(a0)==len(a1)
    n=len(a0)
    if not n:return {'n':0,'mean_paired_difference':None,'ci95':None}
    differences=np.asarray(a1,dtype=float)-np.asarray(a0,dtype=float)
    assert np.isfinite(differences).all()
    rng=np.random.default_rng(analysis_seed)
    draws=rng.integers(0,n,size=(replicates,n));means=differences[draws].mean(axis=1)
    return {'n':n,'a0_mean':float(np.mean(a0)),'a1_mean':float(np.mean(a1)),'mean_paired_difference':float(differences.mean()),'median_paired_difference':float(np.median(differences)),'ci95':[float(x) for x in np.quantile(means,[.025,.975],method='linear')],'ci_method':'PERCENTILE_BOOTSTRAP_WHOLE_PAIRS_50000','analysis_seed':analysis_seed,'replicates':replicates}

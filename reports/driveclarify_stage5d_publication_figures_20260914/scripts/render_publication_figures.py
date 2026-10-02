"""Stage5D: publication rendering of Stage5C artifacts, without scientific reruns."""
from pathlib import Path
import argparse
import hashlib
import json
import sys

import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.collections import PathCollection

OUT = Path(__file__).resolve().parents[1]
ROOT = OUT.parents[1]
SOURCE = ROOT / 'reports/driveclarify_stage5c_final_experiment_freeze_20260914'
SLATE, OCHRE, TEAL, INK, GREY = '#697A8A', '#B58A55', '#357C74', '#323232', '#7B7B7B'
POLICIES = ['NO_CLARIFICATION', 'IMMEDIATE_QUERY', 'DRIVECLARIFY']
PALETTE = dict(zip(POLICIES, [SLATE, OCHRE, TEAL]))
MARKERS = dict(zip(POLICIES, ['s', '^', 'o']))
SHORT = ['No clar.', 'Immediate', 'DriveClarify']
GROUPS = ['Original current', 'Original history', 'Extension current', 'Extension history']
DATA_PATHS = {
    'a': ['plot_data/MAIN_LAYOUT_SUMMARY.csv', 'plot_data/FINAL_MAIN_SUMMARY.csv'],
    'b': ['plot_data/FINAL_ABLATION_EFFECTS_PP.csv'],
    'c': ['plot_data/TRAJECTORY_TASK_POINTS.csv'],
    'd': ['plot_data/TIMING_POINT_DATA.csv', 'evidence/ABLATION_RESULTS.json'],
}
FILENAMES = dict(a='figure_a_main_tradeoff', b='figure_b_ablation_forest',
                 c='figure_c_trajectory_task', d='figure_d_evidence_timing')
RECEIPTS = []


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes(paths):
    freeze = json.loads((OUT / 'qa/SOURCE_FREEZE.json').read_text())
    if sha(SOURCE / 'ARTIFACT_MANIFEST.json') != freeze['source_manifest_sha256']:
        raise RuntimeError('Stage5C manifest identity changed')
    manifest = json.loads((SOURCE / 'ARTIFACT_MANIFEST.json').read_text())
    expected = {r['path']: r['sha256'] for r in manifest['members']}
    actual = {}
    for relative in paths:
        actual[relative] = sha(SOURCE / relative)
        if actual[relative] != expected[relative]:
            raise RuntimeError('Frozen data mismatch: ' + relative)
    return actual


def csv(name):
    return pd.read_csv(SOURCE / 'plot_data' / name, keep_default_na=False)


def theme():
    sns.set_theme(context='paper', style='whitegrid', font='DejaVu Sans')
    plt.rcParams.update({
        'font.family': 'DejaVu Sans', 'font.size': 8.0, 'axes.labelsize': 8.5,
        'axes.titlesize': 9.5, 'axes.titleweight': 'bold', 'xtick.labelsize': 7.5,
        'ytick.labelsize': 7.5, 'legend.fontsize': 7.5, 'text.color': INK,
        'axes.labelcolor': INK, 'axes.edgecolor': GREY, 'axes.linewidth': .6,
        'xtick.color': INK, 'ytick.color': INK, 'xtick.major.width': .6,
        'ytick.major.width': .6, 'xtick.major.size': 2.5, 'ytick.major.size': 2.5,
        'grid.color': '#E9E9E9', 'grid.linewidth': .35, 'lines.linewidth': .8,
        'figure.facecolor': 'white', 'axes.facecolor': 'white', 'savefig.facecolor': 'white',
        'savefig.transparent': False, 'pdf.fonttype': 42, 'ps.fonttype': 42,
        'svg.fonttype': 'none', 'svg.hashsalt': 'driveclarify-stage5d-publication',
    })


def clean(ax, grid='y'):
    sns.despine(ax=ax, top=True, right=True)
    ax.grid(False)
    if grid:
        ax.grid(axis=grid, color='#E9E9E9', linewidth=.35)
    ax.set_axisbelow(True)
    ax.tick_params(pad=2)


def heading(ax, label, title):
    ax.set_title(f'({label}) {title}', loc='left', fontsize=9.5, fontweight='bold', pad=7)


def exact_policy_points(ax, frame, x, y, size_outer=56, size_inner=20):
    """All rows plotted at their exact numeric coordinates; no jitter or aggregation."""
    for split in ['HIST', 'DEV']:
        for p in POLICIES:
            subset = frame[(frame['split'] == split) & (frame['policy'] == p)]
            start = len(ax.collections)
            sns.scatterplot(data=subset, x=x, y=y, ax=ax, marker=MARKERS[p],
                            color=PALETTE[p], s=size_outer if split == 'HIST' else size_inner,
                            linewidth=.8 if split == 'HIST' else .35, legend=False, zorder=4)
            for collection in ax.collections[start:]:
                collection.set_facecolor('white' if split == 'HIST' else PALETTE[p])
                collection.set_edgecolor(PALETTE[p])


def figure_a(single):
    frame = csv('MAIN_LAYOUT_SUMMARY.csv')
    for p in POLICIES:
        subset = frame[frame['policy'] == p]
        q, w = {'NO_CLARIFICATION': (0, 1/6), 'IMMEDIATE_QUERY': (1, 0), 'DRIVECLARIFY': (1/3, 0)}[p]
        assert np.allclose(subset['query'], q) and np.allclose(subset['wrong_task'], w)
        assert subset.groupby('split').size().to_dict() == {'DEV': 8, 'HIST': 14}
    frame = frame.assign(query_pct=100*frame['query'], wrong_pct=100*frame['wrong_task'],
                         policy_row=frame['policy'].map(dict(zip(POLICIES, range(3)))))
    fig = plt.figure(figsize=(3.4, 5.8) if single else (7, 2.85))
    positions = [[.18,.66,.78,.28],[.32,.33,.64,.14],[.32,.07,.64,.13]] if single else [
        [.09,.20,.44,.64],[.72,.64,.26,.21],[.72,.13,.26,.26]]
    main, query, wrong = [fig.add_axes(pos) for pos in positions]
    exact_policy_points(main, frame, 'query_pct', 'wrong_pct')
    main.set(xlim=(-5,107), ylim=(-2.5,23), xlabel='Query rate (%)', ylabel='Wrong-task commitment (%)',
             xticks=[0,33.3333333333,100], yticks=[0,10,20])
    main.set_xticklabels(['0','33.33','100'])
    heading(main, 'a', 'Controlled trade-off')
    annotations = [('NO_CLARIFICATION', 'No clarification\n(0, 16.67)', (4,4), 'left'),
                   ('IMMEDIATE_QUERY', 'Immediate query\n(100, 0)', (-3,10), 'right'),
                   ('DRIVECLARIFY', 'DriveClarify\n(33.33, 0)', (0,10), 'center')]
    for policy, label, offset, align in annotations:
        row = frame[frame['policy']==policy].iloc[0]
        main.annotate(label, (row['query_pct'],row['wrong_pct']),xytext=offset,textcoords='offset points',
                      ha=align,va='bottom',fontsize=7.5)
    main.text(.38,.69,'−66.7 pp queries\n−16.7 pp wrong commitments',transform=main.transAxes,
              fontsize=7.5,ha='left',va='top')
    clean(main, 'y')
    for ax,metric,panel,title,xmax,ticks,ticklabels in [
        (query,'query_pct','b','Layout queries',112,[0,33.3333333333,100],['0','33.3','100']),
        (wrong,'wrong_pct','c','Layout commitments',22,[0,16.6666666667],['0','16.7'])]:
        exact_policy_points(ax,frame,metric,'policy_row',size_outer=35,size_inner=10)
        ax.set(yticks=range(3),yticklabels=SHORT,ylim=(2.6,-.6),xlim=(-3 if metric=='query_pct' else -1,xmax),
               xlabel='Query rate (%)' if metric=='query_pct' else 'Wrong commitment (%)',ylabel='',xticks=ticks)
        ax.set_xticklabels(ticklabels)
        heading(ax,panel,title)
        for i,p in enumerate(POLICIES):
            value=float(frame[frame['policy']==p][metric].iloc[0])
            high=value> .7*xmax
            ax.annotate('8 / 14',(value,i),xytext=(-5 if high else 5,0),textcoords='offset points',
                        ha='right' if high else 'left',va='center',fontsize=7)
        clean(ax,'x')
    fig.text(.18 if single else .09,.55 if single else .045,
             'Exact overlap: DEV filled (n=8), HIST open (n=14)',fontsize=7.5)
    return fig, dict(layout_policy_rows=len(frame), no_jitter=True,
                     points={'NO_CLARIFICATION':[0,16.666666666666668],
                             'IMMEDIATE_QUERY':[100,0], 'DRIVECLARIFY':[33.33333333333333,0]})


def figure_b(single):
    frame=csv('FINAL_ABLATION_EFFECTS_PP.csv')
    groups=[frame[(frame.ablation=='WITHOUT_TASK_GATE') & (frame.metric=='query')],
            frame[(frame.ablation=='WITHOUT_MEMORY') & frame.metric.isin(['evidence_sufficient','actionable_windows'])],
            frame[(frame.ablation=='WITHOUT_TIMING') & (frame.metric=='late')]]
    expected=[[66.66666666666667,66.66666666666667],[-25.53191489361702,-12.76595744680851,-73.91304347826086,-30.434782608695656],
              [36.17021276595745,43.47826086956522]]
    for group,values in zip(groups,expected):assert np.allclose(group.effect_percentage_points,values)
    fig=plt.figure(figsize=(3.4,6.4) if single else (7,2.7))
    positions=[[.34,.75,.62,.14],[.34,.405,.62,.20],[.34,.10,.62,.15]] if single else [
        [.08,.23,.20,.52],[.46,.23,.22,.52],[.82,.23,.16,.52]]
    panel_info=[('a','Task gate','Query rate ↑','DEV (8)\nHIST (14)',(-8,100),[0,50,100]),
                ('b','Temporal memory','Evidence / windows ↓','',(-108,12),[-100,-50,0]),
                ('c','Timing','Late-trigger rate ↑','',(-5,78),[0,25,50,75])]
    for j,(pos,group,info) in enumerate(zip(positions,groups,panel_info)):
        ax=fig.add_axes(pos);label,title,subtitle,_,limits,ticks=info
        labels=[]
        for i,(_,r) in enumerate(group.iterrows()):
            source=r['source']; value=float(r['effect_percentage_points'])
            labels.append((f'{source} ({int(r.denominator)})') if j==0 else
                          (('Orig. ' if source=='ORIGINAL' else 'Ext. ')+('suff.' if r.metric=='evidence_sufficient' else 'window')) if j==1 else
                          ('Original (47)' if source=='ORIGINAL' else 'Extension (23)'))
            filled=source in ['DEV','ORIGINAL']
            sns.scatterplot(x=[value],y=[i],ax=ax,s=31,marker='o' if filled else 's',color=SLATE,
                            edgecolor=SLATE,linewidth=.85,legend=False,zorder=3)
            ax.collections[-1].set_facecolor(SLATE if filled else 'white')
            ax.annotate(f'{value:+.2f}',(value,i),xytext=(-5,0),textcoords='offset points',
                        va='center',ha='right',fontsize=7.5)
            if j==0:
                assert float(r.ci_low_percentage_points)==float(r.ci_high_percentage_points)==value
            else:
                assert r.ci_low_percentage_points==r.ci_high_percentage_points==''
        ax.axvline(0,color=INK,lw=.8,zorder=1)
        ax.set(yticks=range(len(group)),yticklabels=labels,xlim=limits,xticks=ticks,
               ylim=(len(group)-.5,-.5),xlabel='',ylabel='')
        ax.text(0,1.34,f'({label}) {title}',transform=ax.transAxes,fontsize=9.5,fontweight='bold',ha='left')
        ax.text(0,1.17,subtitle,transform=ax.transAxes,fontsize=7.5,ha='left')
        if j==1:ax.text(0,1.04,'47 / 23 complete endpoints',transform=ax.transAxes,fontsize=7,ha='left')
        clean(ax,'x')
    fig.text(.5,.025 if single else .065,'Effect of removing component (percentage points)',ha='center',fontsize=8.5)
    return fig,dict(archived_effects_pp=[g.effect_percentage_points.tolist() for g in groups],
                    horizontal_uncertainty_lines=0, degenerate_gate_intervals_preserved=True)


def deterministic_strip(ax, frame, x, y, order, marker, color, hollow=False, size=3.2):
    """Seaborn strips with deterministic category-only offsets, never y jitter."""
    if frame.empty:
        return
    before=len(ax.collections)
    sns.stripplot(data=frame,x=x,y=y,order=order,jitter=False,ax=ax,marker=marker,
                  color=color,edgecolor=color,linewidth=.6,size=size,zorder=4)
    collections=ax.collections[before:]
    assert len(collections)==len(order)
    for category,collection in zip(order,collections):
        values=frame.loc[frame[x]==category,y].to_numpy(float)
        offsets=np.asarray(collection.get_offsets(),dtype=float)
        assert len(values)==len(offsets)
        if len(values):
            assert np.allclose(offsets[:,1],values)
            offsets[:,0]+=np.linspace(-.14,.14,len(values)) if len(values)>1 else 0
            collection.set_offsets(offsets)
        if hollow:collection.set_facecolor('white')


def figure_c(single):
    frame=csv('TRAJECTORY_TASK_POINTS.csv').sort_values('record_id',kind='stable')
    frame['relation']=frame.truth_relation.map({'TASK_EQUIVALENT':'EQ','TASK_DIVERGENT':'DV','':'Undefined'})
    assert frame.relation.notna().all() and len(frame)==203
    frame['kind']='Other defined'
    frame.loc[frame.truth_relation=='','kind']='Undefined'
    frame.loc[(frame.truth_relation=='TASK_DIVERGENT') & (frame.trajectory_relation=='TASK_EQUIVALENT'),'kind']='Close-DV'
    frame.loc[(frame.truth_relation=='TASK_EQUIVALENT') & (frame.trajectory_relation=='TASK_DIVERGENT'),'kind']='Far-EQ'
    specs=[('C_DEV','DEV',14,1),('C_HIST','HIST',21,14),('B_ABL_FULL','Historical Full',6,3),
           ('B_ABL_TRAJ_ONLY','Historical trajectory-only',5,4)]
    fig,axs=plt.subplots(4,1,figsize=(3.4,8.5)) if single else plt.subplots(2,2,figsize=(7,4.85))
    fig.subplots_adjust(left=.18 if single else .085,right=.98,bottom=.065 if single else .11,
                        top=.935 if single else .88,hspace=.62 if single else .60,wspace=.28)
    legend=[Line2D([],[],marker='v',color=OCHRE,markerfacecolor='white',ls='',label='Close-DV'),
            Line2D([],[],marker='D',color=OCHRE,markerfacecolor='white',ls='',label='Far-EQ'),
            Line2D([],[],marker='o',color=SLATE,ls='',label='Other defined'),
            Line2D([],[],marker='x',color=GREY,ls='',label='Undefined')]
    fig.legend(handles=legend,loc='upper center',bbox_to_anchor=(.55,1),ncol=2 if single else 4,
               frameon=False,handletextpad=.4,columnspacing=.9,fontsize=7.5,markerscale=.7)
    counts=[]
    for i,(ax,(source,title,close_dv,far_eq)) in enumerate(zip(axs.flat,specs)):
        subset=frame[frame.source==source]
        assert (subset.kind=='Close-DV').sum()==close_dv and (subset.kind=='Far-EQ').sum()==far_eq
        threshold=float(subset.frozen_threshold.iloc[0])
        assert subset.frozen_threshold.nunique()==1
        assert np.isclose(threshold,.1 if source.startswith('C_') else .27119792945561905)
        for kind,marker,color,hollow,size in [('Other defined','o',SLATE,False,2.6),('Undefined','x',GREY,False,3),
                                             ('Close-DV','v',OCHRE,True,3.4),('Far-EQ','D',OCHRE,True,3.4)]:
            deterministic_strip(ax,subset[subset.kind==kind],'relation','trajectory_distance',['EQ','DV','Undefined'],marker,color,hollow,size)
        ax.axhline(threshold,color=INK,linestyle=(0,(4,2)),lw=.8,zorder=2)
        ax.text(.03,.92,f'Threshold {threshold:.3f} m',transform=ax.transAxes,fontsize=7.5,va='top')
        ax.text(.98,.72,f'{close_dv} close-DV\n{far_eq} far-EQ',transform=ax.transAxes,fontsize=7.5,ha='right',va='top')
        ax.set(xlim=(-.4,2.4),ylim=(-.025,2),ylabel='Trajectory distance (m)',xlabel='',yticks=[0,.5,1,1.5,2])
        ax.set_xticks(range(3),[f'{cat}\nn={(subset.relation==cat).sum()}' for cat in ['EQ','DV','Undefined']])
        heading(ax,chr(97+i),f'{title} (n={len(subset)})')
        clean(ax,'y')
        counts.append(dict(source=source,n=len(subset),close_DV=close_dv,far_EQ=far_eq,threshold=threshold))
    return fig,dict(source_counts=counts, y_values_unchanged=True, categorical_x_only_offsets=True)


def timing_data():
    frame=csv('TIMING_POINT_DATA.csv')
    frame['endpoint_complete']=frame['endpoint_complete'].astype(str)
    frozen=json.loads((SOURCE/'evidence/ABLATION_RESULTS.json').read_text())['temporal']
    assert len(frame)==144 and (frame.endpoint_complete=='True').sum()==140
    frame['group']=frame.source.map({'ORIGINAL':'Original','EXTENSION':'Extension'})+' '+frame.memory_condition.map({'B1':'current','B2':'history'})
    metrics=['first_evidence_sufficient_time','remaining_margin_s']
    summaries=[]
    for metric in metrics:
        frame[metric]=pd.to_numeric(frame[metric],errors='coerce')
        for source,condition in [('ORIGINAL','B1'),('ORIGINAL','B2'),('EXTENSION','B1'),('EXTENSION','B2')]:
            complete=frame[(frame.source==source)&(frame.memory_condition==condition)&(frame.endpoint_complete=='True')]
            values=complete[metric].dropna()
            key='first_sufficient_time_distribution' if metric.startswith('first') else 'remaining_margin_distribution'
            target=frozen[source]['memory'][condition][key]
            assert len(values)==target['n']
            if len(values):
                assert np.isclose(values.median(),target['median'])
                assert np.allclose(values.quantile([.25,.75]),[target['q1'],target['q3']])
            else:assert target['median'] is None
            summaries.append(dict(metric=metric,source=source,view=condition,n=len(values),
                                  frozen_median=target['median'],complete_missing=int(complete[metric].isna().sum()),
                                  incomplete_views=int(((frame.source==source)&(frame.memory_condition==condition)&(frame.endpoint_complete!='True')).sum())))
    return frame[frame.endpoint_complete=='True'].sort_values('record_id',kind='stable'),summaries


def figure_d(single):
    frame,summaries=timing_data()
    fig,axs=plt.subplots(4,1,figsize=(3.4,8.75)) if single else plt.subplots(2,2,figsize=(7,5.1))
    fig.subplots_adjust(left=.20 if single else .10,right=.98,bottom=.07 if single else .12,
                        top=.915 if single else .89,hspace=.80 if single else .74,wspace=.34)
    handles=[Line2D([],[],marker='s',color=SLATE,markerfacecolor='white',ls='',label='Current observation'),
             Line2D([],[],marker='o',color=TEAL,ls='',label='Retained history')]
    fig.legend(handles=handles,loc='upper center',bbox_to_anchor=(.54,1),ncol=1 if single else 2,
               frameon=False,fontsize=7.5,handletextpad=.4,markerscale=.7)
    counts=[]
    for i,ax in enumerate(axs.flat):
        metric='first_evidence_sufficient_time' if i<2 else 'remaining_margin_s'
        zoom=i%2==1
        low,high= ((0,8) if i<2 else (-3,5)) if zoom else ((-.7,115) if i<2 else (-5,105))
        good=frame[frame[metric].notna()]
        box_palette={g:('#E9EEF2' if 'current' in g else '#DCEBE7') for g in GROUPS}
        sns.boxplot(data=good,x='group',y=metric,hue='group',order=GROUPS,hue_order=GROUPS,
                    palette=box_palette,legend=False,dodge=False,width=.50,saturation=1,showfliers=False,
                    whis=1.5,linewidth=.7,ax=ax,medianprops={'color':INK,'linewidth':.9},
                    boxprops={'edgecolor':GREY},whiskerprops={'color':GREY},capprops={'color':GREY})
        drawn_medians=[]
        for line in ax.lines:
            xs=np.asarray(line.get_xdata(),dtype=float);ys=np.asarray(line.get_ydata(),dtype=float)
            if len(xs)==2 and np.isclose(abs(xs[1]-xs[0]),.50) and np.isclose(ys[0],ys[1]):
                drawn_medians.append(float(ys[0]))
        expected_medians=[r['frozen_median'] for r in summaries if r['metric']==metric and r['n']>0]
        assert np.allclose(drawn_medians,expected_medians)
        for view,color,marker,hollow in [('current',SLATE,'s',True),('history',TEAL,'o',False)]:
            deterministic_strip(ax,good[good.group.str.endswith(view)],'group',metric,GROUPS,marker,color,hollow,2.8)
        ticklabels=[];panel_counts=[]
        for j,group in enumerate(GROUPS):
            values=good.loc[good.group==group,metric];n=len(values);visible=int(values.between(low,high).sum())
            prefix=('Orig.' if group.startswith('Original') else 'Ext.')+'\n'+('Current' if group.endswith('current') else 'History')
            ticklabels.append(prefix+('\n'+f'{visible}/{n}' if zoom else '\n'+f'n={n}'))
            if n==0:ax.text(j,.62,'No sufficient\nevidence',transform=ax.get_xaxis_transform(),ha='center',va='center',fontsize=7)
            panel_counts.append(dict(group=group,visible=visible,total=n))
        if i>=2:ax.axhline(0,color=INK,linestyle=(0,(4,2)),linewidth=.85,zorder=3)
        ax.set(xticks=range(4),xticklabels=ticklabels,xlim=(-.55,3.55),ylim=(low,high),xlabel='',
               ylabel='First sufficient time (s)' if i<2 else 'Intervention margin (s)')
        heading(ax,chr(97+i),'Zoomed view' if zoom else 'Full scale')
        if zoom:ax.text(1,1.045,'Visible / total n',transform=ax.transAxes,ha='right',fontsize=7.5)
        clean(ax,'y')
        counts.append(dict(metric=metric,zoomed_view=zoom,ylim=[low,high],groups=panel_counts,
                           drawn_box_medians=drawn_medians,drawn_medians_match_frozen=True))
    return fig,dict(frozen_summary_checks=summaries,panels=counts,no_missing_zero_imputation=True,
                    boxplot='median/Q1/Q3, 1.5 IQR; showfliers=False; strips contain all finite observations')


def export(fig,key,single,before,info,grayscale=False):
    fig.canvas.draw()
    renderer=fig.canvas.get_renderer()
    escaped=[]
    for item in fig.findobj(matplotlib.text.Text):
        if not item.get_visible() or not item.get_text():continue
        box=item.get_window_extent(renderer)
        if box.x0 < -.5 or box.y0 < -.5 or box.x1 > fig.bbox.width+.5 or box.y1 > fig.bbox.height+.5:
            escaped.append(item.get_text())
    if escaped:raise RuntimeError(f'{key} {single=}: text outside nominal canvas: {escaped}')
    data_counts=[sum(len(c.get_offsets()) for c in ax.collections if isinstance(c,PathCollection)) for ax in fig.axes]
    directory=OUT/'figures'/('single_column' if single else '')
    stem=FILENAMES[key]
    for ext in ['pdf','svg','png']:
        metadata={'Date':None} if ext=='svg' else {'CreationDate':None,'ModDate':None} if ext=='pdf' else None
        fig.savefig(directory/f'{stem}.{ext}',dpi=600,bbox_inches='tight',bbox_extra_artists=[fig.patch],
                    pad_inches=.015,transparent=False,facecolor='white',metadata=metadata)
    # A physical-size inspection copy is a QA view, not an additional scientific figure.
    qa_path=OUT/'qa'/f'{stem}_{"single" if single else "double"}_150dpi.png'
    fig.savefig(qa_path,dpi=150,bbox_inches='tight',bbox_extra_artists=[fig.patch],pad_inches=.015,facecolor='white')
    after=source_hashes(DATA_PATHS[key]);assert before==after
    receipt=dict(figure=key,variant='single_column' if single else 'double_column',source_data_sha256=before,
                 source_hashes_match_after_export=True,script_sha256=sha(Path(__file__)),
                 nominal_size_inches=fig.get_size_inches().tolist(),export_dpi=600,bbox_inches='tight',
                 scatter_points_by_panel=data_counts,text_outside_nominal_canvas=[],checks=info)
    RECEIPTS.append(receipt)
    plt.close(fig)
    print(f'{key.upper()} {receipt["variant"]}: exported PDF/SVG/PNG; source hashes matched',flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--figures',default='abcd');parser.add_argument('--variants',default='double,single')
    args=parser.parse_args();theme()
    existing=[]
    path=OUT/'qa/RENDER_RECEIPTS.json'
    if path.exists():existing=json.loads(path.read_text())['figures']
    for variant in args.variants.split(','):
        for key in args.figures:
            before=source_hashes(DATA_PATHS[key])
            fig,info={'a':figure_a,'b':figure_b,'c':figure_c,'d':figure_d}[key](variant=='single')
            export(fig,key,variant=='single',before,info)
    keys={(r['figure'],r['variant']) for r in RECEIPTS}
    result=dict(versions=dict(seaborn=sns.__version__,matplotlib=matplotlib.__version__,pandas=pd.__version__,numpy=np.__version__),
                scientific_statistics_added=False,figures=[r for r in existing if (r['figure'],r['variant']) not in keys]+RECEIPTS)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()

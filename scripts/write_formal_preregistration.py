#!/usr/bin/env python3
"""Write the immutable formal analysis/preregistration companions."""
from __future__ import annotations
import argparse, json, hashlib, time
from pathlib import Path

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument('--root',type=Path,required=True); args=parser.parse_args(); root=args.root.resolve()
    manifest=json.loads((root/'FORMAL_CAMPAIGN_MANIFEST_SEAL.json').read_text())
    frozen=json.loads((Path(__file__).resolve().parents[1]/'saturated_fixed_work_baseline_v1_3/structured_output_recovery/FINAL_METHOD_FROZEN.json').read_text())
    prereg={
      'schema_version':'membind.formal-preregistration.v1','status':'SEALED',
      'campaign_id':manifest.get('campaign_id'),'manifest_sha256':manifest.get('manifest_sha256'),
      'method_frozen_seal_sha256':frozen.get('seal_sha256'),'history_count':5,'replicate_count':3,'arm_count':3,
      'primary_performance_estimand':'same-history same-replicate paired A_vs_C T_build ratio; B is relaxed-order ceiling',
      'quality_policy':'PAIRED_QUALITY_DELTA_ONLY; report per-question deltas/disagreement and cluster-aware descriptive uncertainty; no automatic non-inferiority claim',
      'qa_contract':{'questions_per_history':60,'total_rows':2700,'gold_mapping_anomaly':'question 38 retained and disclosed'},
      'run_order':{'history_atomic':True,'replicate_orders':{'0':['GRAPHITI_SERIAL_SHARED_BOUNDED_SO','RELAXED_ORDER_SHARED_BOUNDED_SO','MEMBIND_V6_1_SHARED_BOUNDED_SO'],'1':['RELAXED_ORDER_SHARED_BOUNDED_SO','MEMBIND_V6_1_SHARED_BOUNDED_SO','GRAPHITI_SERIAL_SHARED_BOUNDED_SO'],'2':['MEMBIND_V6_1_SHARED_BOUNDED_SO','GRAPHITI_SERIAL_SHARED_BOUNDED_SO','RELAXED_ORDER_SHARED_BOUNDED_SO']}},
      'failure_policy':'NO_RESUME_FORMAL_ATTEMPT','selection_policy':'formal data cannot tune fixed method','uncertainty':'five top-level histories are clusters; descriptive cluster-aware uncertainty only',
      'created_at':time.time()
    }
    prereg['preregistration_sha256']=hashlib.sha256(json.dumps(prereg,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    (root/'FORMAL_PREREGISTRATION.json').write_text(json.dumps(prereg,ensure_ascii=True,sort_keys=True,indent=2)+'\n')
    (root/'FORMAL_PREREGISTRATION.md').write_text('# Formal Preregistration\n\nStatus: `SEALED`. Primary estimand is same-history, same-replicate paired A/C T_build ratio; B is a relaxed-order ceiling. Quality uses paired deltas only, with all 60 questions per history and question 38 disclosed.\n')
    (root/'EARLY_SCIENTIFIC_DIAGNOSTIC.json').write_text(json.dumps({'schema_version':'membind.early-scientific-diagnostic.v1','status':'SEALED_BEFORE_FORMAL_RESULTS','decision':'CONTINUE_UNLESS_PREREGISTERED_SAFETY_PATHOLOGY','selection_use':'NONE','manifest_sha256':manifest.get('manifest_sha256')},sort_keys=True,indent=2)+'\n')
    (root/'EARLY_SCIENTIFIC_DIAGNOSTIC.md').write_text('# Early Scientific Diagnostic\n\nSealed before formal results. No performance or quality observation can alter the frozen method or stop history-atomic execution.\n')
    return 0
if __name__=='__main__': raise SystemExit(main())

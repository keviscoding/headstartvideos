"""Start, inspect, or resume durable Fly discovery without running it on this laptop."""
import argparse
import json
import uuid
from webapp import database as db
from webapp.niche_store import NicheStore

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['start','status','resume'])
    parser.add_argument('--job-id')
    parser.add_argument('--profile',choices=['balanced','avatar'],default='balanced')
    parser.add_argument('--seconds',type=int,default=1800)
    parser.add_argument('--target',type=int,default=20)
    parser.add_argument('--candidates',type=int,default=300)
    parser.add_argument('--reviews',type=int,default=100)
    parser.add_argument('--pages',type=int,default=100)
    parser.add_argument('--api',type=int,default=2000)
    args=parser.parse_args()
    NicheStore.ensure_schema()
    if args.action=='status':
        print(json.dumps(db.get_niche_hunt_run_by_job_id(args.job_id),indent=2));return
    from core.niche_cloud import HuntSettings
    from webapp.fly_bridge import spawn_niche_scrape_machine
    if args.action=='start':
        current=db.get_latest_running_niche_hunt()
        if current: raise SystemExit('A hunt is already running: '+current['job_id'])
        request=dict(profile=args.profile,time_budget_seconds=args.seconds,target_channels=args.target,
            candidate_cap=args.candidates,review_cap=args.reviews,search_cap=args.pages,api_cap=args.api,
            max_subscribers=500000)
        HuntSettings.from_request(request)
        job_id=str(uuid.uuid4())
        run_id=db.create_niche_hunt_run(job_id=job_id,trigger='one_off',keywords=[],request=request)
    else:
        job_id=args.job_id
        run=db.get_niche_hunt_run_by_job_id(job_id)
        if not run or run['status']!='running': raise SystemExit('Only interrupted running hunts can resume within their original budget')
        run_id=run['id']
    mid=spawn_niche_scrape_machine(job_id)
    if not mid:
        if args.action=='start': db.finish_niche_hunt_run(run_id,status='error',error='Could not launch cloud worker')
        raise SystemExit('Could not launch cloud worker')
    db.append_niche_hunt_progress(job_id,'Cloud worker started: '+mid)
    print(json.dumps({'job_id':job_id,'machine_id':mid}))

if __name__=='__main__': main()

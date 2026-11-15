"""Independently verify a downloaded preservation package. No application database required."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.aip_validation import validate_package
from app.packages import sha
parser=argparse.ArgumentParser();parser.add_argument('package');parser.add_argument('--expected-sha256');parser.add_argument('--sources-dir');parser.add_argument('--require-preservation-ready',action='store_true');args=parser.parse_args()
resolver=None
if args.sources_dir:
    paths={sha(p.read_bytes()):p for p in Path(args.sources_dir).glob('*.zip')}
    resolver=lambda digest:paths[digest].read_bytes()
report=validate_package(Path(args.package).read_bytes(),ROOT/'schemas',expected_sha256=args.expected_sha256,source_resolver=resolver)
print(json.dumps(report,indent=2))
sys.exit(0 if report['structural_valid'] and (report['preservation_ready'] or not args.require_preservation_ready) else 1)

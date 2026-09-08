"""Losslessly archive three superseded, pre-leakage-fix prediction files."""
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = [f'benchmark/direct_english_tfidf_v1.2_sd_core_{split}_predictions.jsonl'
         for split in ('validation', 'test', 'ood')]
ARCHIVE = ROOT / 'benchmark/archive/pre_leakage_fix_predictions_20260908.tar.gz'
CHECKED = ['metadata/evidence_binding_manifest.json',
           'metadata/current_revision_manifest_v1.2_sd_core.json',
           'scripts/generate_publication_sd_figures.py',
           'scripts/audit_manuscript_metric_bindings.py',
           'paper/scientific_data_latex/main.tex']


def main():
    if ARCHIVE.exists():
        raise FileExistsError(ARCHIVE)
    for filename in CHECKED:
        content = (ROOT / filename).read_text()
        for selected in FILES:
            if selected in content:
                raise ValueError(f'Current reference in {filename}: {selected}')
        if 'benchmark/direct_english_tfidf_v1.2_sd_core_report.json' in content:
            raise ValueError(f'Original report is current in {filename}')
    sizes = {filename: (ROOT / filename).stat().st_size for filename in FILES}
    ARCHIVE.parent.mkdir(exist_ok=True)
    subprocess.run(['tar', '-czf', str(ARCHIVE), '-C', str(ROOT), *FILES], check=True)
    # Compare every archived byte to its existing original before removing any file.
    with tarfile.open(ARCHIVE, 'r:gz') as archive:
        for filename in FILES:
            with archive.extractfile(filename) as archived, (ROOT / filename).open('rb') as original:
                while True:
                    a, b = archived.read(1024 * 1024), original.read(1024 * 1024)
                    if a != b:
                        raise ValueError(f'Archive bytes differ: {filename}')
                    if not a:
                        break
    report = {'archive': str(ARCHIVE.relative_to(ROOT)), 'files': sizes,
              'original_bytes': sum(sizes.values()), 'archive_bytes': ARCHIVE.stat().st_size,
              'net_saved_bytes': sum(sizes.values()) - ARCHIVE.stat().st_size,
              'verification': 'Every archived member compared byte-for-byte with its original before removal.',
              'current_reference_sources_checked': CHECKED,
              'scope': 'Only three superseded pre-leakage-fix direct-English TF-IDF predictions; current, strict, and other registered evidence retained.'}
    (ARCHIVE.parent / 'pre_leakage_fix_predictions_20260908.json').write_text(json.dumps(report, indent=2)+'\n')
    for filename in FILES:
        (ROOT / filename).unlink()
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

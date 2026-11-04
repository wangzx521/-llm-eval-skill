"""Explicitly build a source-locked, CPU-only EvalPlus image on Linux."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-image", required=True, help="Official Python 3.12 image pinned by sha256 digest")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"python:3\.12[-\w.]*@sha256:[0-9a-f]{64}", args.base_image):
        parser.error("Use a digest-pinned official python:3.12 image")
    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / "src/llm_eval_skills/evalplus-lock.json").read_text())
    output = args.output_dir.resolve()
    if output.exists():
        parser.error("Choose a fresh output directory")
    output.mkdir(parents=True)
    for entry in lock["scoring_core_files"]:
        with urllib.request.urlopen(entry["url"], timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise RuntimeError("Author source hash mismatch for " + entry["path"])
        target = output / "site" / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    shutil.copytree(root / "licenses", output / "licenses")
    dockerfile = '''ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN python -m pip install --no-cache-dir numpy==1.26.4 psutil==7.2.2
COPY site/evalplus /usr/local/lib/python3.12/site-packages/evalplus
COPY licenses /usr/share/licenses/llm-eval-skills
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES="" NVIDIA_VISIBLE_DEVICES=void
USER 65534:65534
'''
    (output / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    image_record = output / "image-id.txt"
    subprocess.run(["docker", "build", "--build-arg", "BASE_IMAGE=" + args.base_image,
                    "--iidfile", str(image_record), str(output)], check=True)
    image = image_record.read_text().strip()
    from llm_eval_skills.sandbox import qualify_image
    qualify_image(image)
    receipt = {"base_image": args.base_image, "image": image, "evalplus_commit": lock["commit"],
               "numpy": "1.26.4", "psutil": "7.2.2", "qualification": "passed"}
    (output / "image.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(image)


if __name__ == "__main__":
    main()

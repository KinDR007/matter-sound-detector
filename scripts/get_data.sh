#!/usr/bin/env bash
# Download the training data and the teacher model into model/data/ (not part of the repo).
#
# Usage: scripts/get_data.sh [--keep-archives] [COMPONENT...]
#   yamnet       YAMNet teacher, TFLite + class map (~4 MB)              [default]
#   esc50        ESC-50, 2000 clips (~0.8 GB)                             [default]
#   fsd50k-eval  FSD50K eval split, 10231 clips (~8 GB, ~14 GB while unpacking)  [default]
#   fsd50k-dev   FSD50K dev split, 40966 clips (~24 GB, ~60 GB while unpacking) [default]
# Re-running is safe: finished components are skipped, interrupted downloads resume.
# Archives are checked against the Zenodo MD5 sums and deleted after unpacking.
#
# Licences: ESC-50 CC BY-NC 3.0, FSD50K clips CC0 / CC BY / CC BY-NC (see its
# metadata), YAMNet Apache-2.0. Models trained on this data inherit the
# non-commercial restriction (see LICENSE-MODEL.md).
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${DATA:-$HERE/model/data}"
KEEP=0
COMPONENTS=()
for a in "$@"; do
	case "$a" in
	--keep-archives) KEEP=1 ;;
	yamnet | esc50 | fsd50k-eval | fsd50k-dev) COMPONENTS+=("$a") ;;
	-h | --help) sed -n '2,15p' "$0"; exit 0 ;;
	*) echo "unknown argument: $a (see --help)" >&2; exit 2 ;;
	esac
done
[ ${#COMPONENTS[@]} -gt 0 ] || COMPONENTS=(yamnet esc50 fsd50k-eval fsd50k-dev)

for tool in curl unzip zip python3; do
	command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 1; }
done
mkdir -p "$DATA"

ZENODO_RECORD=4060432
YAMNET_URL="https://www.kaggle.com/api/v1/models/google/yamnet/tfLite/classification-tflite/1/download"
YAMNET_SHA256="10c95ea3eb9a7bb4cb8bddf6feb023250381008177ac162ce169694d05c317de"
YAMNET_MAP_URL="https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv"
ESC50_URL="https://github.com/karolpiczak/ESC-50/archive/refs/heads/master.zip"

log() { printf '\033[1m%s\033[0m\n' "$*"; }

free_gb() { df -Pk "$DATA" | awk 'NR == 2 {print int($4 / 1048576)}'; }

need_space() { # GB
	local free
	free="$(free_gb)"
	[ "$free" -ge "$1" ] || { echo "need ~$1 GB free in $DATA, have $free GB" >&2; exit 1; }
}

fetch() { # url file: resumable download
	curl -fL --retry 5 --retry-delay 5 -C - -o "$2" "$1"
}

sha256() { python3 -c 'import hashlib,sys;h=hashlib.sha256();f=open(sys.argv[1],"rb")
for b in iter(lambda:f.read(1<<20),b""):h.update(b)
print(h.hexdigest())' "$1"; }

md5() { python3 -c 'import hashlib,sys;h=hashlib.md5();f=open(sys.argv[1],"rb")
for b in iter(lambda:f.read(1<<20),b""):h.update(b)
print(h.hexdigest())' "$1"; }

zenodo_md5() { # file name -> md5 from the Zenodo API
	curl -fsSL "https://zenodo.org/api/records/$ZENODO_RECORD" |
		python3 -c 'import json,sys
for f in json.load(sys.stdin)["files"]:
    if f["key"] == sys.argv[1]: print(f["checksum"].split(":")[1])' "$1"
}

zenodo_fetch() { # name dir: download + MD5 check, skip if already verified
	local name="$1" dir="$2" want
	want="$(zenodo_md5 "$name")"
	[ -n "$want" ] || { echo "$name not found in Zenodo record $ZENODO_RECORD" >&2; exit 1; }
	if [ -f "$dir/$name.ok" ] && [ "$(cat "$dir/$name.ok")" = "$want" ]; then
		return
	fi
	log "download $name"
	fetch "https://zenodo.org/records/$ZENODO_RECORD/files/$name?download=1" "$dir/$name"
	if [ "$(md5 "$dir/$name")" != "$want" ]; then
		rm -f "$dir/$name"
		echo "$name: MD5 mismatch, deleted, run again" >&2
		exit 1
	fi
	echo "$want" >"$dir/$name.ok"
}

get_yamnet() {
	local d="$DATA/yamnet"
	mkdir -p "$d"
	if [ ! -f "$d/1.tflite" ] || [ "$(sha256 "$d/1.tflite")" != "$YAMNET_SHA256" ]; then
		log "download YAMNet"
		fetch "$YAMNET_URL" "$d/yamnet.tar.gz"
		tar -xzf "$d/yamnet.tar.gz" -C "$d"
		rm -f "$d/yamnet.tar.gz"
		[ "$(sha256 "$d/1.tflite")" = "$YAMNET_SHA256" ] || { echo "YAMNet: SHA-256 mismatch" >&2; exit 1; }
	fi
	[ -s "$d/yamnet_class_map.csv" ] || fetch "$YAMNET_MAP_URL" "$d/yamnet_class_map.csv"
	[ "$(wc -l <"$d/yamnet_class_map.csv")" -eq 522 ] || { echo "yamnet_class_map.csv: unexpected size" >&2; exit 1; }
	log "yamnet: ok"
}

get_esc50() {
	local d="$DATA/esc50" n
	mkdir -p "$d"
	if [ "$(find "$d/ESC-50-master/audio" -name '*.wav' 2>/dev/null | wc -l)" -ne 2000 ]; then
		need_space 2
		log "download ESC-50"
		fetch "$ESC50_URL" "$d/master.zip"
		unzip -q -o "$d/master.zip" -d "$d"
		[ "$KEEP" = 1 ] || rm -f "$d/master.zip"
	fi
	n="$(find "$d/ESC-50-master/audio" -name '*.wav' | wc -l | tr -d ' ')"
	[ "$n" -eq 2000 ] || { echo "ESC-50: $n clips, expected 2000" >&2; exit 1; }
	log "esc50: $n clips"
}

get_fsd50k() { # split (eval|dev), number of .zNN parts, space needed in GB
	local split="$1" parts="$2" space="$3" d="$DATA/fsd50k" dl names n expected
	dl="$d/download"
	mkdir -p "$dl"
	zenodo_fetch FSD50K.ground_truth.zip "$dl"
	[ -f "$d/FSD50K.ground_truth/$split.csv" ] || unzip -q -o "$dl/FSD50K.ground_truth.zip" -d "$d"
	expected="$(($(wc -l <"$d/FSD50K.ground_truth/$split.csv") - 1))"
	n="$(find "$d/FSD50K.${split}_audio" -name '*.wav' 2>/dev/null | wc -l | tr -d ' ')"
	if [ "$n" -ne "$expected" ]; then
		need_space "$space"
		names=()
		for i in $(seq 1 "$parts"); do names+=("FSD50K.${split}_audio.z$(printf %02d "$i")"); done
		names+=("FSD50K.${split}_audio.zip")
		for f in "${names[@]}"; do zenodo_fetch "$f" "$dl"; done
		log "unpack FSD50K $split"
		zip -q -s 0 "$dl/FSD50K.${split}_audio.zip" --out "$dl/${split}_full.zip"
		unzip -q -o "$dl/${split}_full.zip" -d "$d"
		rm -f "$dl/${split}_full.zip"
		if [ "$KEEP" = 0 ]; then
			for f in "${names[@]}"; do rm -f "$dl/$f"; done
		fi
		n="$(find "$d/FSD50K.${split}_audio" -name '*.wav' | wc -l | tr -d ' ')"
	fi
	[ "$n" -eq "$expected" ] || { echo "FSD50K $split: $n clips, expected $expected" >&2; exit 1; }
	log "fsd50k-$split: $n clips"
}

for c in "${COMPONENTS[@]}"; do
	case "$c" in
	yamnet) get_yamnet ;;
	esc50) get_esc50 ;;
	fsd50k-eval) get_fsd50k eval 1 14 ;;
	fsd50k-dev) get_fsd50k dev 5 60 ;;
	esac
done
log "done: $DATA"

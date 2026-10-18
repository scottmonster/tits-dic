#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WHISPER_DIR="${1:-${SCRIPT_DIR}/whisper.cpp}"
CLI_FILE="${WHISPER_DIR%/}/examples/cli/cli.cpp"

log() {
  echo "[INFO] $*"
}

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

[[ -f "$CLI_FILE" ]] || die "cli file not found: $CLI_FILE"

if grep -q -- '--max-tokens' "$CLI_FILE" && grep -q 'wparams.suppress_blank' "$CLI_FILE"; then
  log "whisper-cli already has max_tokens and suppress_blank wiring"
  exit 0
fi

perl -0pi - "$CLI_FILE" <<'PERL'
sub ensure {
  my ($ok, $message) = @_;
  die "$message\n" unless $ok;
}

if (index($_, 'int32_t max_tokens    = 0;') < 0) {
  ensure(
    s/(int32_t max_context\s+=\s+-1;\n\s+int32_t max_len\s+=\s+0;\n)/$1    int32_t max_tokens    = 0;\n/m,
    'failed to insert max_tokens field'
  );
}

if (index($_, 'bool suppress_blank  = whisper_full_default_params(WHISPER_SAMPLING_GREEDY).suppress_blank;') < 0) {
  ensure(
    s/(int32_t gpu_device\s+=\s+0;\n)/$1    bool suppress_blank  = whisper_full_default_params(WHISPER_SAMPLING_GREEDY).suppress_blank;\n/m,
    'failed to insert suppress_blank field'
  );
}

if (index($_, '--max-tokens') < 0) {
  ensure(
    s/(else if \(arg == "-mc"\s+\|\| arg == "--max-context"\)\s+\{ params\.max_context\s+= std::stoi\(ARGV_NEXT\); \}\n\s+else if \(arg == "-ml"\s+\|\| arg == "--max-len"\)\s+\{ params\.max_len\s+= std::stoi\(ARGV_NEXT\); \}\n)/$1        else if (arg == "-mt"   || arg == "--max-tokens")           { params.max_tokens      = std::stoi(ARGV_NEXT); }\n/m,
    'failed to add max-tokens parser'
  );
}

if (index($_, '--no-suppress-blank') < 0) {
  ensure(
    s/(else if \(arg == "-fa"\s+\|\| arg == "--flash-attn"\)\s+\{ params\.flash_attn\s+= true; \}\n\s+else if \(arg == "-nfa"\s+\|\| arg == "--no-flash-attn"\)\s+\{ params\.flash_attn\s+= false; \}\n)/$1        else if (arg == "-sb"   || arg == "--suppress-blank")       { params.suppress_blank  = true; }\n        else if (arg == "-nsb"  || arg == "--no-suppress-blank")    { params.suppress_blank  = false; }\n/m,
    'failed to add suppress-blank parser'
  );
}

if (index($_, 'maximum number of tokens per segment') < 0) {
  ensure(
    s/(fprintf\(stderr, "  -mc N,     --max-context N        \[%\-7d\] maximum number of text context tokens to store\\n", params\.max_context\);\n\s+fprintf\(stderr, "  -ml N,     --max-len N            \[%\-7d\] maximum segment length in characters\\n",\s+params\.max_len\);\n)/$1    fprintf(stderr, "  -mt N,     --max-tokens N         [%-7d] maximum number of tokens per segment\\n",           params.max_tokens);\n/m,
    'failed to add max-tokens help text'
  );
}

if (index($_, '--no-suppress-blank    ') < 0) {
  ensure(
    s/(fprintf\(stderr, "  -fa,       --flash-attn           \[%\-7s\] enable flash attention\\n",\s+params\.flash_attn \? "true" : "false"\);\n\s+fprintf\(stderr, "  -nfa,      --no-flash-attn        \[%\-7s\] disable flash attention\\n",\s+params\.flash_attn \? "false" : "true"\);\n)/$1    fprintf(stderr, "  -sb,       --suppress-blank       [%-7s] suppress blank outputs\\n",                         params.suppress_blank ? "true" : "false");\n    fprintf(stderr, "  -nsb,      --no-suppress-blank    [%-7s] disable blank suppression\\n",                      params.suppress_blank ? "false" : "true");\n/m,
    'failed to add suppress-blank help text'
  );
}

if (index($_, 'wparams.max_tokens       = params.max_tokens;') < 0) {
  ensure(
    s/(wparams\.thold_pt\s+= params\.word_thold;\n\s+wparams\.max_len\s+= params\.output_wts && params\.max_len == 0 \? 60 : params\.max_len;\n)/$1            wparams.max_tokens       = params.max_tokens;\n/m,
    'failed to wire max_tokens into wparams'
  );
}

if (index($_, 'wparams.suppress_blank   = params.suppress_blank;') < 0) {
  ensure(
    s/(wparams\.no_timestamps\s+= params\.no_timestamps;\n\n)/$1            wparams.suppress_blank   = params.suppress_blank;\n/m,
    'failed to wire suppress_blank into wparams'
  );
}
PERL

log "Patched ${CLI_FILE}"
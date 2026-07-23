#!/usr/bin/env bash
set -Eeuo pipefail

REPO_DIR="$HOME/GalkaLive"
BRANCH="agent/galka-live-hardening-v3"
BASE_COMMIT="d5a52564b047ab9e00f154d3816dab053fad417a"
BUNDLE_INPUT="${1:-}"
IMPORT_REF="refs/galka-bundle-import/$$"
IMPORT_REF_CREATED=0

die() {
  printf 'BUNDLE IMPORT STOPPED: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  if [[ "$IMPORT_REF_CREATED" -eq 1 ]]; then
    git -C "$REPO_DIR" update-ref -d "$IMPORT_REF" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

for required in bash git; do
  command -v "$required" >/dev/null 2>&1 || die "не найдена обязательная команда: $required"
done

[[ -n "$BUNDLE_INPUT" && -z "${2:-}" ]] || {
  printf 'Usage: bash scripts/import-production-bundle.sh <production.bundle>\n' >&2
  exit 2
}
[[ -f "$BUNDLE_INPUT" && ! -L "$BUNDLE_INPUT" ]] || \
  die "bundle не найден, не является обычным файлом или является symlink: $BUNDLE_INPUT"
BUNDLE_DIR="$(cd "$(dirname "$BUNDLE_INPUT")" && pwd -P)"
BUNDLE="$BUNDLE_DIR/$(basename "$BUNDLE_INPUT")"

[[ "$REPO_DIR" != "/" && "$REPO_DIR" != "$HOME" ]] || \
  die "небезопасный путь репозитория: $REPO_DIR"
[[ -d "$REPO_DIR" ]] || die "не найден каталог репозитория: $REPO_DIR"
git -C "$REPO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || \
  die "$REPO_DIR не является Git worktree"
CURRENT_BRANCH="$(git -C "$REPO_DIR" branch --show-current)"
[[ "$CURRENT_BRANCH" == "$BRANCH" ]] || \
  die "ожидалась ветка $BRANCH, текущая: ${CURRENT_BRANCH:-detached HEAD}"
[[ -z "$(git -C "$REPO_DIR" status --porcelain=v1 --untracked-files=all)" ]] || \
  die "рабочее дерево содержит изменения; импорт отменён"

printf '[1/4] Проверяю целостность Git bundle...\n'
git -C "$REPO_DIR" bundle verify "$BUNDLE"
mapfile -t BUNDLE_HEAD_ROWS < <(
  git bundle list-heads "$BUNDLE"
)
[[ "${#BUNDLE_HEAD_ROWS[@]}" -eq 1 && \
   "${BUNDLE_HEAD_ROWS[0]#* }" == "refs/heads/$BRANCH" ]] || \
  die "bundle должен содержать только production-ветку: $BRANCH"
BUNDLE_HEAD="${BUNDLE_HEAD_ROWS[0]%% *}"
[[ "$BUNDLE_HEAD" =~ ^[0-9a-f]{40,64}$ ]] || die "bundle содержит некорректный HEAD"
git -C "$REPO_DIR" show-ref --verify --quiet "$IMPORT_REF" && \
  die "временный import ref уже существует: $IMPORT_REF"

printf '[2/4] Импортирую production-ветку во временный ref...\n'
git -C "$REPO_DIR" fetch --no-tags "$BUNDLE" \
  "refs/heads/$BRANCH:$IMPORT_REF"
IMPORT_REF_CREATED=1
IMPORTED_HEAD="$(git -C "$REPO_DIR" rev-parse "$IMPORT_REF^{commit}")"
[[ "$IMPORTED_HEAD" == "$BUNDLE_HEAD" ]] || \
  die "импортированный HEAD не совпадает с объявленным bundle HEAD"
git -C "$REPO_DIR" cat-file -e "$BASE_COMMIT^{commit}"
git -C "$REPO_DIR" merge-base --is-ancestor "$BASE_COMMIT" "$IMPORTED_HEAD" || \
  die "bundle не продолжает проверенную production base"
[[ "$(git -C "$REPO_DIR" rev-list --merges --count "$BASE_COMMIT..$IMPORTED_HEAD")" == "0" ]] || \
  die "bundle содержит merge-коммиты в production-диапазоне"
for required_path in \
  PRODUCTION_READINESS_REPORT.md \
  scripts/import-production-bundle.sh \
  scripts/termux-sync-and-prepare-galka.sh \
  scripts/test-termux-sync-and-prepare-galka.sh \
  scripts/test-import-production-bundle.sh \
  scripts/setup-galka-live.sh \
  scripts/verify-galka-live.sh \
  scripts/check-galka-live-account.sh; do
  git -C "$REPO_DIR" cat-file -e "$IMPORTED_HEAD:$required_path" || \
    die "bundle не содержит обязательный файл: $required_path"
done
for executable_path in \
  scripts/import-production-bundle.sh \
  scripts/termux-sync-and-prepare-galka.sh \
  scripts/setup-galka-live.sh \
  scripts/verify-galka-live.sh \
  scripts/check-galka-live-account.sh; do
  [[ "$(git -C "$REPO_DIR" ls-tree "$IMPORTED_HEAD" "$executable_path" | awk '{print $1}')" == "100755" ]] || \
    die "bundle содержит неверный executable mode: $executable_path"
done

LOCAL_HEAD="$(git -C "$REPO_DIR" rev-parse HEAD)"
git -C "$REPO_DIR" merge-base --is-ancestor "$LOCAL_HEAD" "$IMPORTED_HEAD" || \
  die "bundle не является fast-forward для текущей локальной ветки"

printf '[3/4] Выполняю только fast-forward до bundle HEAD...\n'
git -C "$REPO_DIR" merge --ff-only "$IMPORT_REF"
[[ "$(git -C "$REPO_DIR" rev-parse HEAD)" == "$BUNDLE_HEAD" ]] || \
  die "локальная ветка не достигла bundle HEAD"
[[ -z "$(git -C "$REPO_DIR" status --porcelain=v1 --untracked-files=all)" ]] || \
  die "импорт оставил изменения в рабочем дереве"

printf '[4/4] Запускаю installer, preflight и read-only подготовку...\n'
cd "$REPO_DIR"
bash scripts/termux-sync-and-prepare-galka.sh --prepare-local "$BUNDLE_HEAD"

printf '\nBUNDLE IMPORT PASS\n'
printf 'PRODUCTION HEAD: %s\n' "$BUNDLE_HEAD"
printf 'LIVE OFF\n'
printf 'ORDERS SENT: NO\n'
printf 'READY\n'

#!/usr/bin/env bash
# deploy/limpiar_ramas.sh — borra en origin las ramas que ya están 100% contenidas en main.
#
# Por qué existe: cada sesión de Claude Code web pushea a una rama `claude/<slug>`; después
# esa rama se mergea a main a mano y nadie la borra. Sin PR, GitHub tampoco la borra sola.
# Se acumularon 99. Este script deja solo lo que tiene trabajo que main no tiene.
#
# Uso (desde cualquier checkout con credenciales de push, NO hace falta el Droplet):
#   bash deploy/limpiar_ramas.sh                      → LISTA candidatas, no borra nada
#   bash deploy/limpiar_ramas.sh --borrar             → borra las contenidas en main
#   bash deploy/limpiar_ramas.sh --borrar rama1 rama2 → además borra esas por nombre exacto
#                                                       (para descartar trabajo que se decidió no mergear)
# Nunca toca: main, la rama actual, dependabot/* (son PRs abiertos de dependencias).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

modo="listar"
if [[ "${1:-}" == "--borrar" ]]; then modo="borrar"; shift; fi
extras=("$@")

git fetch --prune --quiet origin
actual=$(git rev-parse --abbrev-ref HEAD)

mapfile -t candidatas < <(
  git for-each-ref --format='%(refname:short)' refs/remotes/origin \
    | sed 's#^origin/##' \
    | grep -v -e '^main$' -e '^HEAD$' -e '^dependabot/' -e "^${actual}$" \
    | while read -r r; do
        git merge-base --is-ancestor "origin/$r" origin/main && echo "$r" || true
      done
)

echo "Ramas 100% contenidas en main (borrables sin perder nada): ${#candidatas[@]}"
printf '  %s\n' "${candidatas[@]:-}"
if ((${#extras[@]})); then
  echo "Además, por nombre (trabajo descartado a propósito): ${#extras[@]}"
  for r in "${extras[@]}"; do
    git show-ref --verify --quiet "refs/remotes/origin/$r" || { echo "  ✗ no existe en origin: $r"; exit 1; }
    n=$(git rev-list --count "origin/main..origin/$r")
    echo "  $r  (+$n commits que main no tiene)"
  done
fi

if [[ "$modo" != "borrar" ]]; then
  echo; echo "Modo lista. Para borrar: bash deploy/limpiar_ramas.sh --borrar [ramas extra…]"; exit 0
fi

todas=("${candidatas[@]:-}" "${extras[@]}")
todas=("${todas[@]}")  # normaliza
[[ -z "${todas[*]// }" ]] && { echo "Nada para borrar."; exit 0; }

# De a 25 para que un push no se quede sin aire.
lote=(); borradas=0
for r in "${todas[@]}"; do
  [[ -z "$r" ]] && continue
  lote+=("$r")
  if ((${#lote[@]} == 25)); then git push origin --delete "${lote[@]}"; borradas=$((borradas+${#lote[@]})); lote=(); fi
done
((${#lote[@]})) && { git push origin --delete "${lote[@]}"; borradas=$((borradas+${#lote[@]})); }
git fetch --prune --quiet origin
echo; echo "Borradas: $borradas. Quedan en origin:"
git for-each-ref --format='  %(refname:short)' refs/remotes/origin | sed 's#origin/##' | grep -v HEAD

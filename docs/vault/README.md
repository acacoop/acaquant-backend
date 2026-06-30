# Vault acaquant — cómo usarlo

1. Instalá [Obsidian](https://obsidian.md).
2. *Open folder as vault* → elegí `docs/vault/`.
3. Abrí `Home.md` y el **graph view**.

## Qué hay adentro

- **250** module
- **97** component
- **53** route
- **34** cron
- **29** collection
- **20** view
- **15** service
- **14** lib

## Regenerar

```bash
python -m scripts.gen_obsidian          # regenera
python -m scripts.gen_obsidian --check  # CI: falla si quedó stale
```

La sección _Qué hace_ de cada nota la completa la pasada de
enriquecimiento con IA; el resto (archivos, links, backlinks) es
determinista y se regenera del código.

# MTools Shared Media Runtime

Staging local para o monorepo público `github.com/mtutida/MTools`.

Este pacote documenta o Shared Media Runtime e os candidatos CompactMe 0.1.1. Os artefatos atuais permanecem `UNSIGNED_CANDIDATE`; links de download só devem ser preenchidos após publicação autorizada.

## Componentes

- `apps/compactme/` — integração do aplicativo;
- `runtime/shared-media/` — catálogo, manifestos e resolver;
- `installers/` — receitas dos instaladores Full, AppOnly e Runtime;
- `docs/` — arquitetura, validação, licenças e release;
- `.github/workflows/` — build e validação reproduzíveis.

## Build e rastreabilidade

Cada release deve registrar commit, workflow run, SBOM, manifestos, hashes pré/pós-assinatura e aprovação manual SignPath. A rc395 e a instalação oficial estão fora deste escopo.

## Downloads

Os URLs de download serão adicionados somente após promoção autorizada. Não usar os candidatos não assinados como release.

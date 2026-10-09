# Mapeamento de origem e build

| Artefato | Origem | Hash pré-assinatura | Build/evidência |
|---|---|---|---|
| Full 0.1.1 | `apps/compactme` + payload bundled | `71F4325C0B4398770D476DF54686542A26DA884E77683D9F1FD8E07F93E66603` | workflow/run a registrar no monorepo |
| AppOnly 0.1.1 | `apps/compactme` + integração shared | `C9F9AB11B5023C9E7D9F43E2699AEF7AE347FC225BFCAE8A12CAB4761210E1F5` | workflow/run a registrar no monorepo |
| Runtime 0.1.1 | `runtime/shared-media` | `86838C6E17A72BDEBC28AFEBEDC42727EBBB4967ED142C229C7B302F570E07DA` | workflow/run a registrar no monorepo |

Os hashes identificam candidatos não assinados; qualquer rebuild ou assinatura exige novo manifesto.

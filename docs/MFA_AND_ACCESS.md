# MFA e acesso

- MFA obrigatório para a conta do proprietário e qualquer conta administrativa.
- Tokens e certificados não entram no repositório ou nos logs.
- Workflows usam `permissions: contents: read` por padrão.
- Build/validação não pode assinar, publicar ou promover.
- Mudanças de permissões devem ser registradas junto ao projeto SignPath.

# Deploy automático na VPS

O deploy de produção é disparado automaticamente quando o workflow `test` conclui com sucesso para um `push` na branch `main`.

Fluxo:

```text
push/merge em main
      ↓
workflow test
      ↓
sucesso
      ↓
workflow deploy-vps
      ↓
SSH na VPS
      ↓
git pull --ff-only origin main
      ↓
uv sync --frozen
      ↓
systemctl restart turnaround
      ↓
healthcheck local do Streamlit
```

## Configuração única

Crie uma chave SSH exclusiva para o GitHub Actions acessar a VPS:

```bash
ssh-keygen -t ed25519 -C "github-actions-turnaround" -f ~/.ssh/github_actions_turnaround -N ""
```

Adicione o conteúdo de `~/.ssh/github_actions_turnaround.pub` ao arquivo `~/.ssh/authorized_keys` do usuário `ubuntu` na VPS.

Permita que o usuário reinicie somente o serviço do Turnaround sem senha. Edite:

```bash
sudo visudo -f /etc/sudoers.d/turnaround-deploy
```

e adicione:

```text
ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl restart turnaround.service, /usr/bin/systemctl status turnaround.service
```

Valide:

```bash
sudo visudo -cf /etc/sudoers.d/turnaround-deploy
```

No GitHub, em **Settings → Secrets and variables → Actions**, crie:

- `VPS_HOST`: IP ou hostname público da VPS;
- `VPS_USER`: `ubuntu`;
- `VPS_SSH_KEY`: conteúdo da chave privada `~/.ssh/github_actions_turnaround`;
- `VPS_KNOWN_HOSTS`: saída de `ssh-keyscan -H <VPS_HOST>`.

A chave privada usada pelo Actions deve ser exclusiva para deploy e não deve ser reutilizada para acesso pessoal.

## Operação

Depois da configuração inicial, nenhum `git pull` manual é necessário.

Se um teste falhar, o deploy não acontece.

Se o restart ocorrer mas o healthcheck não responder, o workflow falha e mostra o status do serviço.

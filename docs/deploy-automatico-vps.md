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
checkout do SHA exato aprovado pelos testes
      ↓
uv sync --frozen
      ↓
systemctl restart turnaround
      ↓
healthcheck local do Streamlit
```

## Provisionamento do serviço

O workflow automático desta instalação usa **uv + systemd + Caddy**, sem Docker.

Antes de habilitar o deploy automático, a VPS precisa ter o ambiente sincronizado e o unit do serviço instalado:

```bash
cd /home/ubuntu/turnaround-project
uv sync --frozen

sudo cp deploy/systemd/turnaround.service /etc/systemd/system/turnaround.service
sudo systemctl daemon-reload
sudo systemctl enable --now turnaround.service
```

Valide:

```bash
systemctl status turnaround.service --no-pager
curl -fsS http://127.0.0.1:8501/_stcore/health && echo
```

O arquivo versionado usado como fonte é:

```text
deploy/systemd/turnaround.service
```

## Configuração única

Crie uma chave SSH exclusiva para o GitHub Actions acessar a VPS:

```bash
ssh-keygen -t ed25519 -C "github-actions-turnaround" -f ~/.ssh/github_actions_turnaround -N ""
```

Adicione o conteúdo de `~/.ssh/github_actions_turnaround.pub` ao arquivo `~/.ssh/authorized_keys` do usuário `ubuntu` na VPS.

O deploy atualiza também o unit file versionado do systemd. O usuário de deploy precisa executar sem senha os comandos usados pelo script. Edite:

```bash
sudo visudo -f /etc/sudoers.d/turnaround-deploy
```

Em uma VPS dedicada a este serviço, configure as permissões necessárias para instalar o unit file e controlar o serviço, de acordo com a política de sudo da máquina. O fluxo executado é:

```text
install -m 0644 deploy/systemd/turnaround.service /etc/systemd/system/turnaround.service
systemctl daemon-reload
systemctl stop turnaround.service
systemctl start turnaround.service
```

O script usa `sudo -n`: se qualquer uma dessas operações exigir senha, o deploy falhará de forma explícita em vez de ficar aguardando interação.

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

O deploy **não publica simplesmente a ponta atual de `main`**. Quando o workflow `test` termina verde, o `deploy-vps` recebe o SHA exato daquele run, busca a `main` remota apenas para validar que o commit pertence a ela e faz checkout/reset exatamente para esse SHA antes de reiniciar o serviço.

Isso evita a corrida em que um commit mais novo chega à `main` enquanto o deploy anterior está aguardando. Um run verde só pode publicar o commit que ele realmente testou.

Deploys obsoletos também são ignorados se o servidor já estiver em um descendente mais novo daquele SHA.

Se um teste falhar, o deploy não acontece.

Se o serviço não estiver provisionado, se o SHA não corresponder ao esperado ou se o healthcheck não responder, o workflow falha.

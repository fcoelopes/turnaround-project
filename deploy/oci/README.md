# Deploy na OCI

Arquitetura:

Internet -> Caddy (80/443, TLS automático) -> Streamlit (8501 somente na rede Docker)

## 1. VM

Ubuntu 24.04 LTS.

A OCI Ampere A1 Flex (ARM64) é adequada para esta aplicação e também pode ser Always Free quando houver capacidade disponível na home region.

## 2. Rede OCI

Prefira um Network Security Group (NSG) associado à VNIC da VM.

Ingress recomendado:

- TCP 22: somente do seu IP/CIDR administrativo
- TCP 80: 0.0.0.0/0
- TCP 443: 0.0.0.0/0
- UDP 443: 0.0.0.0/0 (HTTP/3 do Caddy, opcional)

Não abra a porta 8501 na OCI.

## 3. DNS

Crie um registro A apontando o domínio/subdomínio para o IPv4 público da VM.

Exemplo:

turnaround.seudominio.com -> IP_PUBLICO_DA_VM

## 4. Instalar Docker no Ubuntu

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
```

Saia e entre novamente na sessão SSH.

Valide:

```bash
docker version
docker compose version
```

## 5. Clonar e configurar

```bash
git clone https://github.com/fcoelopes/turnaround-project.git
cd turnaround-project

cp .env.example .env
nano .env
```

Defina:

```env
DOMAIN=turnaround.seudominio.com
```

## 6. Subir

```bash
docker compose up -d --build
```

Acompanhe:

```bash
docker compose ps
docker compose logs -f app
docker compose logs -f caddy
```

## 7. Atualizar versões futuras

```bash
git pull --ff-only
docker compose up -d --build
docker image prune -f
```

## Teste temporário sem domínio

No arquivo `.env`:

```env
DOMAIN=:80
```

Depois:

```bash
docker compose up -d --build
```

Acesse:

```text
http://IP_PUBLICO_DA_VM
```

Quando o DNS estiver pronto, altere `.env` para o domínio real e rode `docker compose up -d` novamente.

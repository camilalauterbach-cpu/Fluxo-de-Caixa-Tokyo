O Render hospeda o Tokyo completo: interface, backend Python e SQLite no mesmo
endereço HTTPS. O domínio `onrender.com` e o certificado são fornecidos pelo
serviço. O Blueprint usa uma única instância Starter e um disco persistente de
1 GB; o proprietário confere e autoriza o custo apresentado pelo Render.

Abra [a publicação automática no Render](https://render.com/deploy?repo=https://github.com/camilalauterbach-cpu/Fluxo-de-Caixa-Tokyo),
autorize sua conta e informe os três campos privados: seu e-mail, uma senha
própria de 10 a 200 caracteres e a chave de dados de 43 caracteres recebida
na conversa. Confirme os recursos e o valor na tela do Render. Quando o serviço
ficar saudável, abra o endereço HTTPS mostrado por ele e use seu e-mail e senha.

O primeiro início autentica o pacote cifrado e cria o banco no disco persistente.
Reinícios e novas versões preservam o banco existente e as credenciais. Os
snapshots enviados inicializam uma instalação nova; alterações feitas depois
em um Tokyo local precisam ser migradas separadamente para aparecer na web.

A imagem contém somente o backend aprovado, a interface compilada, dependências
e o payload cifrado. Os dados financeiros não entram em claro no repositório,
no contexto de build nem nas camadas da imagem. O processo prepara o diretório
persistente e reduz os privilégios para UID/GID 10001 antes de iniciar o app.
O primeiro acesso público não pode criar um administrador: a conta é provisionada
pelos campos privados do Render antes de liberar o servidor.

A interface conectada ao Lovable pode usar esse mesmo backend por um proxy
seguro no próprio domínio da interface. O endereço do backend é uma
configuração pública do servidor do proxy; credenciais e dados financeiros
continuam protegidos no backend. A publicação do backend no Render continua
sendo necessária, mesmo quando a interface é aberta pelo Lovable.

O SQLite fica em `/var/data/private/tokyo.sqlite3`. Backups cifrados são guardados
no mesmo disco em `private/backups`, usando uma chave distinta gerada pelo Render.
Essas cópias ajudam na recuperação de alterações, mas não substituem uma cópia
externa caso o disco ou o serviço seja excluído. Guarde a chave de backups em
local privado e acompanhe o espaço usado no disco. A API mantém autenticação,
checagem de origem, cookies seguros e controle de versão para edição simultânea.

Para atualizar, publique uma versão revisada na branch conectada; o Render
faz o deploy automaticamente no serviço existente. Mantenha o mesmo disco e
uma única instância: essa instalação usa
SQLite. Não recrie o banco nem copie os snapshots sobre um banco já existente.

Uma instalação própria opcional usa `docker-compose.yml` com Nginx. Copie
`.env.example` para `.env`, preencha os segredos e `TOKYO_PUBLIC_URL` com o seu
endereço HTTPS, e coloque um certificado válido e sua chave em
`tls/fullchain.pem` e `tls/privkey.pem`. Execute `docker compose up -d --build`.
Somente o proxy publica as portas 80 e 443; o backend permanece na rede privada.
A renovação do certificado e uma cópia externa do volume ficam a cargo dessa
instalação própria. O Render já fornece domínio e TLS, sem esses passos.

# Tokyo — aplicativo completo para demonstração

Este pacote contém o aplicativo React/Vite e o servidor Python, com as revisões de calendário, sincronização e conferência de realizados. É um projeto completo, não um patch. Todos os registros de entrada deste pacote são fictícios. A instalação Windows existente não foi alterada.

## Executar sem recompilar

Requer Python 3.12 ou posterior. O backend usa apenas a biblioteca padrão.

```sh
python -B server/app.py --port 8844
```

Abra http://127.0.0.1:8844. Em uma base nova, crie seu primeiro acesso na tela. Os arquivos frontend compilados já estão em dist/. A primeira execução cria uma base de demonstração nova em private/; não copie a base de produção para testar.

## Trabalhar na interface

Requer Node.js 20.19+ ou 22.12+ e pnpm.

```sh
pnpm install --frozen-lockfile
pnpm run build
```

Mantenha o servidor Python em execução. Para desenvolvimento, pnpm run dev usa o proxy configurado para a API na porta 8844.

## Antes de hospedar

O servidor atual ainda é voltado ao uso local. A próxima tarefa deve adaptar bind/PORT, hostname, HTTPS e cookies para o provedor escolhido, revisar o servidor HTTP de produção, configurar banco persistente e backup, e preparar o primeiro administrador de forma segura. Não presuma que basta enviar dist/ para uma hospedagem estática. Não há PWA nem hospedagem configurada neste pacote.

Use um repositório privado para o código. Não envie private/, bancos, credenciais ou documentos financeiros ao Git. O banco real deve ser migrado separadamente após validação. Este pacote não contém uma migração dos dados reais.

## Estado da validação

A revisão de origem passou em 149 testes Python e nove testes de modelo do calendário; o novo calendário ainda tem validação visual pendente. Este pacote de demonstração recebe uma verificação separada de inicialização e cálculo. Os testes legados dependentes de dados reais não são distribuídos aqui.

Os campos e regras de negócio do Tokyo foram conservados; apenas os registros iniciais e saldos de demonstração são sintéticos. Não interprete os dados de exemplo como posição financeira da Tokyo.

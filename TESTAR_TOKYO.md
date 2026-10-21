# Abrir o Tokyo para testar

O pacote portátil funciona no Windows 10/11 de 64 bits. A distribuição contém somente dados fictícios, sem banco financeiro ou credenciais.

1. Abra [o arquivo ZIP](downloads/Fluxo-de-Caixa-Tokyo-Testar.zip) e clique em **Download raw file** (ícone de download).
2. No Windows, clique com o botão direito no ZIP e escolha **Extrair Tudo**.
3. Abra a pasta **Tokyo-Teste** e dê dois cliques em **INICIAR_TOKYO.cmd**.
4. Crie seu acesso na primeira abertura e mantenha a janela do servidor aberta durante os testes.

O Python oficial já vem incluído. O navegador abre em **http://127.0.0.1:8845** enquanto o programa estiver em execução nesse computador.

## O que mudou

- No registro de pagamentos e recebimentos: bancos **Itaú** e **Cora**.
- Observações prontas: **Fluxo já previsto** e **Fluxo fora do previsto**. A opção **Livre** permite escrever outra observação.
- No plano do fornecedor: **Nome fantasia**, **Contato da negociação** e **Telefone**, todos opcionais.
- A busca encontra esses contatos. **Exportar fornecedores (CSV)** e **Imprimir fornecedores** usam a lista filtrada na tela, incluindo os filtros de situação e responsável.

Os dados de contato não mudam valores, parcelas ou condições de negociação. Registrar um pagamento continua exigindo o valor, a data e a referência do comprovante.

## Atualizar sem perder os registros salvos

1. Encerre a demonstração com **Ctrl+C** na janela do servidor.
2. Faça uma cópia de segurança da pasta **Tokyo-Teste** inteira, com o programa fechado.
3. Extraia o novo ZIP em uma pasta temporária.
4. Copie o conteúdo da nova pasta **Tokyo-Teste** para dentro da pasta que você já utiliza, substituindo os arquivos de mesmo nome. **Mantenha a pasta private/**.
5. Abra **INICIAR_TOKYO.cmd** na pasta atualizada.

O ZIP não contém a pasta `private/`. Seu banco local fica em `private/tokyo-demonstracao.sqlite3` e é preservado nessa atualização. A instalação original na porta 8844 continua separada.

## Usar o banco real do Tokyo

Com a demonstração fechada, abra **IMPORTAR_DADOS_REAIS.cmd** e selecione o banco SQLite mais recente da instalação original. O importador valida a compatibilidade, cria uma cópia consistente e guarda um backup da base de destino antes de substituí-la. A origem permanece preservada.

O inicializador `Tokyo.exe` enviado nesta conversa guarda sua configuração em `%USERPROFILE%\Applications\Tokyo\settings.json`. O campo `db_path` indica o arquivo utilizado pela instalação original. O seletor usa essa configuração apenas para sugerir a pasta: você confere e escolhe o arquivo. Se precisar localizá-lo manualmente, cole `%USERPROFILE%\Applications\Tokyo` na barra de endereço do Explorador de Arquivos e abra `settings.json` com o Bloco de Notas.

A importação traz o banco inteiro, incluindo acessos, cenários, histórico e baixas. Depois, entre com o acesso da instalação original. Não há mesclagem automática dos registros de demonstração com o banco importado: a base anterior fica no backup. Se o arquivo não for um banco compatível do Tokyo, a importação é recusada.

Use a base que contém as últimas baixas e entradas. Notas fiscais e planilhas avulsas não identificam, por si só, quais títulos já foram pagos. O banco real fica no seu computador; não o envie ao GitHub.

## Código e verificações

O código atualizado está em `demo/`, com o manifesto da versão distribuída em `demo/manifest.json`. A distribuição pode ser reproduzida com `tools/build_portable.py`; as instruções estão em `tools/portable/README.md`.

O runtime é Python 3.13.16 x64 do pacote oficial PSF no NuGet, com tamanho e SHA-512 verificados. A licença, a origem e os hashes dos arquivos acompanham o ZIP. Os testes da aplicação e dos auxiliares usam Linux e Chromium; o executável Windows não foi executado nativamente neste ambiente.

Este pacote abre localmente. Ele não cria hospedagem pública nem sincronização entre computadores.

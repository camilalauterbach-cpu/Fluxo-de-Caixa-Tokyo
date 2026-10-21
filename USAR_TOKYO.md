# Tokyo — versão de trabalho

[Baixar a versão de trabalho](downloads/Fluxo-de-Caixa-Tokyo-Trabalho.zip). No GitHub, use **Download raw file** (ícone de download).

Esta versão contém a aplicação aprovada, os dados reais enviados e os documentos de referência. Como o repositório está público, os arquivos financeiros ficam protegidos por criptografia dentro do download. A chave de abertura é fornecida na conversa e não acompanha o repositório.

## Abrir no Windows

1. Feche o Tokyo que estiver aberto. Preserve a instalação anterior.
2. Baixe o ZIP novo e escolha **Extrair Tudo**, em uma pasta nova.
3. Abra **Tokyo-Trabalho** e dê dois cliques em **INICIAR_TOKYO.cmd**.
4. Na primeira abertura, cole a chave recebida na conversa. Ela pode não aparecer enquanto você cola; pressione Enter. Nas próximas aberturas, a chave não será solicitada para o mesmo pacote já desbloqueado.
5. O navegador abrirá em **http://127.0.0.1:8845**. Crie seu acesso e comece a atualizar os registros.

O Python e os componentes necessários acompanham o pacote. Requer Windows 10/11 de 64 bits. Mantenha a janela do servidor aberta durante o uso; encerre com Ctrl+C.

## Dados e ajustes incluídos

A base financeira tem corte em **19/10/2026**. Os movimentos posteriores devem ser registrados com os comprovantes correspondentes. Os arquivos enviados não contêm um banco SQLite com baixas mais recentes.

As condições de negociação permanecem propostas em estudo. As restrições vigentes de parcelas são preservadas, e os parâmetros históricos de gestão continuam editáveis. Os documentos originais e versões anteriores ficam em `referencias/`; não são somados à base consolidada.

No registro de pagamentos e recebimentos, escolha **Itaú** ou **Cora**. As observações prontas são **Fluxo já previsto** e **Fluxo fora do previsto**; **Livre** permite outra observação.

No plano de cada fornecedor, **Dados de contato da negociação** oferece nome fantasia, contato e telefone, todos opcionais. A busca encontra esses campos. **Exportar fornecedores (CSV)** e **Imprimir fornecedores** usam a lista filtrada.

## Onde ficam os registros

Depois de abrir, os registros ficam no computador, em `Tokyo-Trabalho/private/tokyo.sqlite3`. Reabrir o programa preserva esse banco. A instalação anterior e seus dados permanecem separados.

Se existir um banco SQLite mais recente na instalação original, **IMPORTAR_DADOS_REAIS.cmd** permite selecioná-lo, validar a compatibilidade e copiá-lo integralmente, fazendo backup da base de trabalho antes da troca. Depois da importação, use o acesso daquela instalação. As bases não são mescladas automaticamente.

Para fazer backup da versão de trabalho, encerre o programa e copie a pasta **Tokyo-Trabalho** inteira. Para atualizar os arquivos do programa, mantenha a pasta `private/`.

Os testes da aplicação, importação e desbloqueio foram executados em Linux e Chromium. Os componentes Windows têm origem e integridade verificadas; a execução nativa no Windows não foi validada neste ambiente.

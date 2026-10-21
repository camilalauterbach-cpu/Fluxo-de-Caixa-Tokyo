# Abrir o Tokyo para testar

Esta branch contém a demonstração original e um pacote portátil para Windows 10/11 de 64 bits. Usa apenas dados fictícios; não contém banco financeiro ou credenciais.

1. Abra [o arquivo ZIP](downloads/Fluxo-de-Caixa-Tokyo-Testar.zip) e clique em **Download raw file** (ícone de download).
2. No Windows, clique com o botão direito no ZIP e escolha **Extrair Tudo**.
3. Abra a pasta **Tokyo-Teste** e dê dois cliques em **INICIAR_TOKYO.cmd**.
4. O navegador deverá abrir. Crie seu acesso de demonstração na primeira tela e mantenha a janela do servidor aberta durante os testes.

O Python oficial vem incluído; não precisa instalar Python ou Node separadamente. A demo usa a porta 8845 e seu próprio banco em private/tokyo-demonstracao.sqlite3, preservado entre aberturas. A instalação financeira existente na porta 8844 fica separada.

O código-fonte original está em demo/. Os 38 hashes do manifesto permanecem idênticos. O runtime Windows incluído é Python 3.13.16 x64 do pacote oficial PSF no NuGet, com tamanho e SHA-512 verificados; licença e origem estão dentro do ZIP.

O inicializador passou em dez verificações em Linux/Python 3.12.14. O executável Windows foi inspecionado como PE x64 e teve sua integridade conferida; não houve execução nativa no Windows neste ambiente.

Este é um pacote local para começar a testar. Não cria hospedagem pública, PWA ou sincronização entre computadores.

SHA-256 do ZIP: 5c1607551c8be2e0b94b332fce1fd7b9c88084144f8fe85d56944ae5c85153c9

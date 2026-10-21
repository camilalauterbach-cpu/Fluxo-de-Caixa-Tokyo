# Recriar o pacote de teste do Windows

Na raiz do repositório, depois de compilar `demo/dist/`, executar as verificações
da aplicação e atualizar `demo/manifest.json`:

```sh
python -B tools/build_portable.py
```

O resultado substitui `downloads/Fluxo-de-Caixa-Tokyo-Testar.zip` somente depois
de conferir o manifesto dos fontes, o SHA-512 do Python e a integridade do ZIP.
O programa imprime o tamanho e o SHA-256 do arquivo final. O build usa somente
a biblioteca padrão do Python e o runtime Windows x64 oficial da PSF no NuGet,
fixado em `python-source.json`. Verificações TLS e de checksum permanecem ativas.
Sem pacote em cache, o download é feito da URL HTTPS oficial e verificado antes
de qualquer uso. O cache padrão fica na pasta temporária do sistema.

Para reutilizar um pacote NuGet oficial já baixado, ainda com conferência SHA-512:

```sh
python -B tools/build_portable.py --python-package /caminho/python-3.13.16.nupkg
```

Para conferir sem gerar/substituir o download:

```sh
python -B tools/build_portable.py --verify-only
```

Os fontes vêm de `demo/`, não da cópia antiga fora do repositório. Todo arquivo
listado no manifesto deve existir e corresponder ao hash atual. Bancos, `private/`,
`.env`, dependências instaladas, caches e resultados temporários não entram no ZIP.
O empacotador não atualiza o manifesto, não altera fontes nem modifica bancos.

Os auxiliares de abertura e as instruções ficam nesta pasta. O runtime acompanha
sua licença e informações de origem; não é necessário instalar Python no Windows.
A execução nativa do binário Windows não foi validada no ambiente Linux.

O pacote inclui também `importar_dados.py` e `IMPORTAR_DADOS_REAIS.cmd` para
uma cópia local e opcional de banco SQLite escolhido pelo usuário. O auxiliar
não faz upload, não mescla registros e preserva a origem; uma base de destino
preexistente recebe backup antes da troca. Nenhum banco entra no arquivo distribuído.

O caminho do banco da demonstração continua sendo
`Tokyo-Teste/private/tokyo-demonstracao.sqlite3`. Para atualizar um teste existente,
fechar a demonstração, copiar toda a pasta como backup e mesclar os arquivos novos
na pasta atual. O ZIP não contém `private/`; não apagar essa pasta durante a atualização.

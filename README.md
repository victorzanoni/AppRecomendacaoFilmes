# Recomendações de filmes a partir do seu Letterboxd

Lê o seu histórico público do Letterboxd, enriquece cada filme com dados do TMDB
(gêneros, diretor, elenco, sinopse, pôster), monta uma base limpa e mostra no navegador
uma página com filmes que combinam com o seu gosto. **Nada que você já assistiu aparece.**

```
Letterboxd (RSS)  ──►  identifica filmes  ──►  TMDB (detalhes)  ──►  base limpa (SQLite + CSV)  ──►  recomendações  ──►  página web
```

---

## Passo a passo (primeira vez)

### 1. Instale o Python
Precisa do **Python 3.9 ou mais novo**. Baixe em <https://www.python.org/downloads/>.
No Windows, marque a caixa **"Add Python to PATH"** durante a instalação.

### 2. Abra o terminal dentro da pasta do projeto
- **Windows:** abra a pasta no Explorador, clique na barra de endereço, digite `cmd` e Enter.
- **macOS:** clique com o botão direito na pasta > *Novo Terminal na Pasta*.
- **Linux:** botão direito na pasta > *Abrir no terminal*.

### 3. Instale as dependências (uma vez só)

```bash
python -m venv .venv
```
Ative o ambiente:
- Windows: `.venv\Scripts\activate`
- macOS/Linux: `source .venv/bin/activate`

```bash
pip install -r requirements.txt
```
> No Windows, se `python` não for reconhecido, use `py` no lugar. No macOS/Linux, pode ser `python3`.
> Sempre que abrir um terminal novo, ative o ambiente de novo (comando `activate` acima).

### 4. Crie sua chave gratuita do TMDB
1. Crie uma conta em <https://www.themoviedb.org/signup>.
2. Vá em <https://www.themoviedb.org/settings/api> e solicite uma chave (tipo *Developer*, uso pessoal; pode escrever que é um projeto pessoal).
3. Copie a **"Chave da API"** (32 caracteres). O **"Token de Leitura da API"** (começa com `eyJ`) também funciona.

### 5. Configure o projeto
Copie `.env.example` para `.env` (Windows: `copy .env.example .env` · macOS/Linux: `cp .env.example .env`), abra o `.env` num editor de texto e preencha:

```
LETTERBOXD_USER=seu_usuario        # o que aparece em letterboxd.com/SEU_USUARIO
TMDB_API_KEY=cole_a_chave_aqui
```
O resto é opcional. Para **trocar de pessoa**, basta mudar `LETTERBOXD_USER` e rodar a atualização de novo (a base antiga é descartada automaticamente para não misturar históricos).

### 6. Rode
```bash
python main.py update          # baixa o histórico, enriquece e calcula as recomendações
python main.py serve --open    # abre http://127.0.0.1:8000 no navegador
```
Ou os dois de uma vez: `python main.py run --open`.

A primeira atualização leva de alguns segundos a poucos minutos. As seguintes são bem mais rápidas, porque os detalhes dos filmes ficam em cache.
Para parar o servidor: `Ctrl + C` no terminal.

---

## Importante: o RSS só mostra os ~50 registros mais recentes

O Letterboxd não tem API pública; o único acesso oficial sem login é o RSS, e ele traz apenas as
**~50 entradas mais recentes do diário**. Um filme só aparece lá se foi **registrado no diário**
(só dar nota ou marcar como visto não entra no feed).

Para o histórico completo (recomendado, as sugestões ficam bem melhores):

1. No Letterboxd: **Settings > Data > Export your data** e baixe o `.zip`.
2. Rode: `python main.py update --export caminho/para/letterboxd-export.zip`

Você só precisa fazer isso uma vez: a base **acumula** entre atualizações, então depois o RSS comum
só soma os filmes novos ao que já está salvo.

---

## O que cada comando faz

| Comando | O que faz |
|---|---|
| `python main.py update` | Atualiza a base e recalcula as recomendações |
| `python main.py update --export arquivo.zip` | Igual, incluindo o histórico completo exportado |
| `python main.py serve [--port 8080] [--open]` | Abre a página web (só lê a base, não precisa de internet nem de chave) |
| `python main.py run [--open]` | `update` + `serve` |

Arquivos gerados na pasta `data/`:
- `cinema.db`: a base (pode apagar para recomeçar do zero).
- `watched_enriched.csv`: seus filmes já limpos, com gêneros, diretores e duração. Abre em qualquer planilha.

---

## Como os dados são tratados

| Situação | O que o app faz |
|---|---|
| Filme sem nota / sem ano / sem data | Aceita. Sem nota, só "ter visto" conta pouco e o coração conta bastante |
| Mesmo filme visto várias vezes | Vira uma linha só (nota mais recente, coração se algum registro tiver) |
| Itens do RSS que não são filmes (listas) | Ignorados |
| Título sem ID do TMDB (exportação) | Busca no TMDB comparando títulos de forma aproximada (ignora acentos, maiúsculas, artigos, ordem das palavras, título original) **e confere o ano**, o que distingue refilmagens como *Dune* 1984 × 2021 |
| Nada parecido o bastante | Fica como **não identificado** (aparece no rodapé da página). Prefere não adivinhar errado |
| Filme não identificado, mas assistido | Continua sendo excluído das sugestões, por título + ano |
| Já assistido | Nunca é recomendado. Conferido por ID do TMDB, por título + ano, e de novo ao abrir a página |
| Chamadas ao TMDB que falham | Novas tentativas automáticas (inclui o limite de requisições); chave inválida gera mensagem clara |

### Corrigindo um filme "não identificado" à mão
Crie `data/manual_matches.csv` (há um exemplo em `data/manual_matches.example.csv`):

```csv
title,year,tmdb_id
Título como aparece no Letterboxd,2004,12345
```
O `tmdb_id` é o número no final do endereço do filme no TMDB (`themoviedb.org/movie/`**`12345`**-nome). Rode `update` de novo.

---

## Como as recomendações são calculadas

1. **Seu gosto:** cada filme tem uma "afinidade" (nota alta e coração pesam a favor; nota baixa pesa contra; leva em conta o quanto você costuma dar notas altas). Ela é somada nos gêneros, diretor, elenco, palavras-chave, década e idioma de cada filme.
2. **Candidatos:** para seus filmes preferidos, o TMDB informa "quem viu este também gostou de...". Filmes que aparecem para várias dessas "sementes" ganham pontos.
3. **Nota final:** 50% semelhança com seu gosto · 35% quantas sementes o recomendaram · 15% qualidade (nota do TMDB suavizada pelo número de votos).
4. **Cada sugestão explica o motivo**, por exemplo *"Porque você gostou de X e Y"*.

Para ajustar, edite o `.env`: `NUM_RECOMMENDATIONS`, `MAX_SEED_FILMS`, `MAX_CANDIDATES`, `MIN_VOTES` (diminua para aparecerem filmes menos conhecidos). Os pesos ficam no topo de `lbrecs/recommender.py`.

---

## Problemas comuns

| Mensagem / sintoma | O que fazer |
|---|---|
| `Defina LETTERBOXD_USER...` / `Defina TMDB_API_KEY...` | O `.env` não existe ou ainda tem os valores de exemplo |
| `Usuário '...' não encontrado` | Confira o nome em `letterboxd.com/SEU_USUARIO`. O perfil precisa ser público |
| `O Letterboxd bloqueou a requisição` (403/429) | Espere alguns minutos, ou use `--export`. Se já houver base salva, o app continua com ela |
| `O TMDB recusou a chave (HTTP 401)` | Copie a chave de novo, sem espaços ou aspas |
| Poucas recomendações | Use a exportação completa; ou reduza `MIN_VOTES` |
| Página sem pôsteres | Os pôsteres vêm do site do TMDB: precisa de internet no navegador |
| `Address already in use` | Outra coisa usa a porta: `python main.py serve --port 8080` |

---

## Para quem quer mexer no código

```
main.py                  linha de comando (update / serve / run)
lbrecs/
  config.py              lê o .env
  letterboxd.py          RSS + exportação do Letterboxd
  matching.py            casa título+ano com um filme do TMDB (comparação aproximada)
  tmdb.py                cliente da API do TMDB (retries, cache de formato)
  cleaning.py            normalização, validação e remoção de duplicatas
  db.py                  SQLite: base, cache e resultados
  pipeline.py            orquestra a atualização (os 5 passos)
  recommender.py         motor de recomendação (sem rede, fácil de testar)
  web.py + templates/    página web (Flask)
tests/test_app.py        testes que rodam offline
```

Rodar os testes (não usam internet nem chave): `python -m unittest discover -s tests -v`

## Avisos
- Este produto usa a API do TMDB, mas não é endossado nem certificado pelo TMDB. Não é afiliado ao Letterboxd.
- Feito para uso pessoal, com o seu próprio perfil público. Respeite os termos de uso do Letterboxd e do TMDB.

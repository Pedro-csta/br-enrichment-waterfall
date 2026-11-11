# Relatório de enriquecimento — 2026-11-11

- Arquivo: `data/empresas_exemplo.csv`
- Linhas lidas: 14 · CNPJs únicos: 13 · válidos: 12
- Cascata: brasilapi → cnpjws → cnpja → receitaws
- Campos obrigatórios: razao_social, situacao, cnae_principal, municipio, uf, telefone, email

## Status

| Status | Empresas |
| --- | --- |
| completo | 11 |
| parcial | 1 |
| nao_encontrado | 0 |
| cnpj_invalido | 1 |

## Cobertura por campo (base: 12 CNPJs válidos)

| Campo | Só brasilapi | Cascata | Ganho |
| --- | --- | --- | --- |
| razao_social * | 100% | 100% | +0 p.p. |
| nome_fantasia | 50% | 50% | +0 p.p. |
| situacao * | 100% | 100% | +0 p.p. |
| data_abertura | 100% | 100% | +0 p.p. |
| porte | 100% | 100% | +0 p.p. |
| natureza_juridica | 100% | 100% | +0 p.p. |
| cnae_principal * | 100% | 100% | +0 p.p. |
| cnae_descricao | 100% | 100% | +0 p.p. |
| municipio * | 100% | 100% | +0 p.p. |
| uf * | 100% | 100% | +0 p.p. |
| cep | 100% | 100% | +0 p.p. |
| telefone * | 100% | 100% | +0 p.p. |
| email * | 0% | 92% | +92 p.p. |
| capital_social | 100% | 100% | +0 p.p. |
| setor | 100% | 100% | +0 p.p. |
| dominio | 0% | 92% | +92 p.p. |

\* campo obrigatório: a cascata só chama o próximo provedor se ele faltar.

**Campos obrigatórios preenchidos:** 86% só com brasilapi → 99% com a cascata.

## Custo em chamadas

| Provedor | Chamadas à API | Cache | Falhas | Campos que preencheu |
| --- | --- | --- | --- | --- |
| brasilapi | 12 | 0 | 0 | 150 |
| cnpjws | 12 | 0 | 0 | 11 |
| cnpja | 1 | 0 | 0 | 0 |
| receitaws | 1 | 0 | 0 | 0 |

A parada antecipada usou 26 de 48 consultas possíveis (46% evitadas).

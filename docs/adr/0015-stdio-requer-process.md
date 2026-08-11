# ADR 0015 - Stdio implica capability process

- Status: aceito
- Data: 2026-08-10
- Relacionado: ADR 0011 (planejamento/autoridade), ADR 0012 (descoberta segura), ADR 0013 (authority/approval), ADR 0014 (ordem parcial dos guards)

## Contexto

O transporte `stdio` materializa um adapter que inicia um processo externo. Antes
do F1, manifests e estados programaticos podiam declarar apenas `read`; o
descriptor chegava ao registry e o efeito `process` ficava invisivel para
planning, grants, task authority e approval.

## Decisao

Toda tool de um manifest `transport: "stdio"` deve declarar explicitamente a
capability `process`. A regra e validada no parser canonico, no adapter defensivo
e na composicao de bootstrap/materializacao; ausencia falha fechado antes de
registro ou spawn. `process` continua uma capability compartilhada: grants de
aplicacao, task authority e approval permanecem camadas distintas e
independentes. `--yes` apenas escolhe approval para a execucao corrente.

## Consequencias

- manifests antigos sem `process` sao rejeitados e nao iniciam subprocessos;
- descriptors validos expoem `process` ao planning e ao gateway;
- ausencia de grant, task authority ou approval continua produzindo a negativa
  correspondente, sem uma camada substituir a outra;
- nao ha mudanca no protocolo stdio, lifecycle ou politica de grants persistentes.

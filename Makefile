PYTHON ?= python3

.PHONY: dados testes verificar extrair

dados:
	$(PYTHON) gerar_dados.py

testes:
	$(PYTHON) -m unittest discover -s testes -v

verificar: dados testes
	git diff --exit-code -- dados/manifesto.csv dados/processados/auditoria.json dados/processados/faccoes_bairro_2010_2022.csv

extrair:
	$(PYTHON) extrair_mapas.py --inicio 2010 --fim 2022

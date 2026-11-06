# Testovací aplikace (PyQt6)

[English](README.en.md)

Tlačítko pro každou Matter událost (třídu zvuku). Kliknutí přehraje z reproduktoru Macu další testovací klip této třídy, deska je připojená přes USB a její log se zobrazuje v konzoli aplikace. Detekovaná událost rozsvítí tlačítko své třídy zeleně a v konzoli je vidět, jestli odpovídá přehrávanému zvuku.

```sh
test/run.sh
```

- Klipy: validační část ESC-50 / FSD50K (model je neviděl), kouřový alarm syntetický, vlastní nahrávky `model/data/own/<třída>*.wav` jsou první v pořadí.
- **Každá klasifikace**: `snd verbose 1`, ukazatel hladiny na mikrofonu a nejpravděpodobnější třída. Hladina by při přehrávání měla být aspoň −55 dBFS.
- **Pravé tlačítko** na třídě: `snd test <třída>`, Matter událost bez zvuku (test strany Home Assistant).
- **Přehrát vše**: jeden klip od každé třídy, pauza 6 s (cooldown na desce je 5 s).
- Řádek dole posílá libovolný příkaz shellu (`snd classes`, `matter ...`).
- Sériový monitor (`scripts/fw.sh monitor`) musí být zavřený, port může mít otevřený jen jeden program.

Běží ve virtuálním prostředí `model/.venv` (potřebuje datasety, viz [docs/training.cs.md](../docs/training.cs.md)); `run.sh` doinstaluje `requirements.txt`, pokud chybí.

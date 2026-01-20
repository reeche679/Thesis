import importlib
import units_and_year_standings as u



importlib.reload(u)

print("max cap y1 t1:", u.max_units_for(1, 1))
print("alias units_for y1 t1:", u.units_for(1, 1))

print(u.sem_index(4, 2))
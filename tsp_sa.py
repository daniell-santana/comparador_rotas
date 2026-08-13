"""
Simulated Annealing para o TSP puro (1 veículo, sem capacidade, sem janela
de horário, sem jornada máxima). Mantido como alternativa mais simples para
efeito de comparação com o solver de VRP, em cenários reais com restrições
operacionais.
"""
import math
import random


def resolver_sa(D, temp_inicial=1000, temp_final=1, alpha=0.97, max_iter=200):
    n = D.shape[0]
    sol = list(range(n))
    random.shuffle(sol[1:])  # o depósito (índice 0) fica fixo como ponto de partida

    def custo(rota):
        return sum(D[rota[i], rota[(i + 1) % n]] for i in range(n))

    melhor_sol, melhor_custo = sol.copy(), custo(sol)
    T = temp_inicial
    while T > temp_final:
        for _ in range(max_iter):
            novo = sol.copy()
            i, j = random.sample(range(1, n), 2)
            novo[i], novo[j] = novo[j], novo[i]
            delta = custo(novo) - custo(sol)
            if delta < 0 or random.random() < math.exp(-delta / T):
                sol = novo
                if custo(sol) < melhor_custo:
                    melhor_sol, melhor_custo = sol.copy(), custo(sol)
        T *= alpha
    return melhor_sol, melhor_custo

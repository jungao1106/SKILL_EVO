import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

y = np.linspace(1000e2, 1, 100)
x = np.exp(-np.linspace(0, 1, y.size))

fig, ax = plt.subplots()
ax.plot(x, y)
ax.set_yscale('log')
ax.set_ylim(y.max(), y.min())
left, right = ax.get_ylim()
print(f"ylim: {left}, {right}")
assert left > right, "Axis not inverted!"

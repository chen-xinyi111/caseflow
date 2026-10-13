"""Schedule independent cases; each case keeps its declared mode order."""
import threading
from concurrent.futures import ThreadPoolExecutor
def execute_pairs(cases,fn,workers=2):
    if workers not in (1,2):raise ValueError('最多两组案例并行')
    stop=threading.Event()
    def one(case):
        for row in case:
            if stop.is_set():return
            if fn(row) is False:stop.set();return
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='opslens-evaluation') as pool:
        list(pool.map(one,cases))

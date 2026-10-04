import numpy as np
from bart import find_barts
vol=np.full(40,1e5); vol[25]=1e6
c=np.r_[np.full(25,10.0),[14,14.3,13.9,14.1,10.5],np.full(10,10.4)]
e=find_barts(c,vol); print(e)
assert [x['side'] for x in e]==['short'] and e[0]['entry']==29 and e[0]['flat_days']==3 and abs(e[0]['target']-10)<1e-9
c2=np.r_[np.full(25,10.0),[7,7.1,6.9,7.0]]
e=find_barts(c2,vol[:29]); print(e)
assert e[0]['side']=='long' and e[0]['entry'] is None and abs(e[0]['trigger']-7.1)<1e-9 and abs(e[0]['stop']-6.9)<1e-9
c3=np.r_[np.full(25,10.0),[7,7.1,6.9,8.0]]
e=find_barts(c3,vol[:29]); print(e); assert e[0]['entry']==28
print("ok")

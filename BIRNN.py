import torch 
import torch.nn as nn   

def encode_choice(probs):
    # probs of size (B, L, 3)
    choice = torch.argmax(probs, dim = -1)
    choice[choice == 2] = -1 # -> (B,L)
    
    return choice

def decode_choice(choice):
    probs = torch.zeros(choice.shape + (3,))
    
    m0 = (choice == 0)[...,None].repeat(1,1,3)
    m0[...,[1,2]] = False
    probs[m0] = 1

    m1 = (choice == 1)[...,None].repeat(1,1,3)
    m1[...,[0,2]] = False
    probs[m1] = 1

    m2 = (choice == -1)[...,None].repeat(1,1,3)
    m2[...,[0,1]] = False
    probs[m2] = 1

    return probs

class ResMLP(nn.Module):
    def __init__(self, in_dim, out_dim, hidden):
        super().__init__()
        self.head = nn.Linear(in_dim, hidden[0])
        self.layers = nn.ModuleList([nn.Linear(hidden[i], hidden[i+1]) for i in range(len(hidden)-1)])
        self.tail = nn.Linear(hidden[-1], out_dim)
        self.activ = nn.ReLU()

    def forward(self, x):
        x = self.head(x)
        for l in self.layers:
            x = x + self.activ(l(x))

        return self.tail(x)
    
class nBRC(nn.Module): #extend to multiple layers ?
    def __init__(self, in_sz, mem_sz, mem_lay = 1, bias = False, batch_first = True):
        super().__init__()
        self.ff_ia = nn.Linear(in_sz, mem_sz, bias = bias)
        self.ff_ha = nn.Linear(mem_sz, mem_sz, bias = bias)

        self.ff_ic = nn.Linear(in_sz, mem_sz, bias = bias)
        self.ff_hc = nn.Linear(mem_sz, mem_sz, bias = bias)

        self.ff_io = nn.Linear(in_sz, mem_sz, bias = bias)
        self.mem_sz = mem_sz

    def step(self, x, h, bist = False): #x of the form (B,N), h -> (B,M)
        a = 1 + torch.tanh(self.ff_ia(x) + self.ff_ha(h))
        c = torch.sigmoid(self.ff_ic(x) + self.ff_hc(h))

        hfn = c*h + (1-c)*torch.tanh(self.ff_io(x) + a*h)
        if bist:
            return a,c, hfn
        
        return hfn
    
    def forward(self, u, h0 = None, mem = False): #u -> (B,L,N), h0 initial mem (B,M)
        B, L, _ = u.shape
        if h0 is None:
            h0 = torch.zeros((B, self.mem_sz)).to(u)

        h_t = [h0]
        if mem:
            al,cl = [],[]
        for i in range(L):
            if mem:
                a,c,h_next= self.step(u[:,i], h_t[-1], bist = True)
                al.append(a)
                cl.append(c)
            else:
                h_next = self.step(u[:,i], h_t[-1])
            h_t.append(h_next)
        
        h_t = [h.unsqueeze(1) for h in h_t[1:]]

        if mem:
            a_t = [at.unsqueeze(1) for at in al]
            c_t = [ct.unsqueeze(1) for ct in cl]
            
            return torch.cat(a_t, dim = 1),\
                   torch.cat(c_t, dim = 1),\
                   torch.cat(h_t, dim = 1)
        
        return torch.cat(h_t, dim = 1), h_t[-1] # (B,L,M)

class nBEFRC(nn.Module): #extend to multiple layers ?
    def __init__(self, in_sz, mem_sz, mem_lay = 1, bias = False, batch_first = True, dt = .1):
        super().__init__()
        self.ff_ia = nn.Linear(in_sz, mem_sz, bias = bias)
        self.ff_ha = nn.Linear(mem_sz, mem_sz, bias = bias)

        self.ff_ib = nn.Linear(in_sz, mem_sz, bias = bias)
        self.ff_hb = nn.Linear(mem_sz, mem_sz, bias = bias)

        self.ff_ic = nn.Linear(in_sz, mem_sz, bias = bias)
        self.ff_hc = nn.Linear(mem_sz, mem_sz, bias = bias)

        self.ff_id = nn.Linear(in_sz, mem_sz, bias = bias)
        self.ff_hd = nn.Linear(mem_sz, mem_sz, bias = bias)

        self.ff_ie = nn.Linear(in_sz, mem_sz, bias = bias)
        self.ff_he = nn.Linear(mem_sz, mem_sz, bias = bias)

        self.ff_io = nn.Linear(in_sz, mem_sz, bias = bias)
        self.mem_sz = mem_sz
        self.dt = dt

    def step(self, x, hf, hs, bist = False): #x of the form (B,N), h -> (B,M)
        a = 1 + torch.tanh(self.ff_ia(x) + self.ff_ha(hf)) #a in [0, 2]
        b = (3/2)*(1 + torch.tanh(self.ff_ib(x) + self.ff_hb(hf))) #b in [0, 3]
        c = 3*self.dt + (1-3*self.dt)*torch.sigmoid(self.ff_ic(x) + self.ff_hc(hf)) #fast 1/tau in [3, 10]
        d = .3*self.dt*torch.sigmoid(self.ff_id(x) + self.ff_hd(hf)) #slow epsilon in [0, .3] (one order below the fast)
        e = 1 + torch.sigmoid(self.ff_ie(x) + self.ff_he(hf)) #e in [1,2]

        hfn = (1-c)*hf + c*torch.tanh(self.ff_io(x) + (a + b*hf**2 - hs)*hf)
        hsn = hs*(1-d) + d*(e*hf)**4
        if bist:
            return a,b,c,d,e, hfn, hsn
        
        return hfn, hsn
                
    
    def forward(self, u, h0 = None, mem = False): #u -> (B,L,N), h0 initial mem (B,M)
        B, L, _ = u.shape
        if h0 is None:
            h0 = torch.zeros((2, B, self.mem_sz)).to(u)

        hf_t = [h0[0]]
        hs_t = [h0[1]]
        if mem:
            al,bl,cl,dl,el = [],[],[],[],[]
        for i in range(L):
            if mem:
                a,b,c,d,e,hf_next, hs_next = self.step(u[:,i], hf_t[-1], hs_t[-1], bist = True)
                al.append(a)
                bl.append(b)
                cl.append(c)
                dl.append(d)
                el.append(e)
            else:
                hf_next, hs_next = self.step(u[:,i], hf_t[-1], hs_t[-1])
            hf_t.append(hf_next)
            hs_t.append(hs_next)
        
        h_t = [h.unsqueeze(1) for h in hf_t[1:]]
        
        if mem:
            a_t = [at.unsqueeze(1) for at in al]
            b_t = [bt.unsqueeze(1) for bt in bl]
            c_t = [ct.unsqueeze(1) for ct in cl]
            d_t = [dt.unsqueeze(1) for dt in dl]
            e_t = [et.unsqueeze(1) for et in el]
            
            return torch.cat(a_t, dim = 1),\
                   torch.cat(b_t, dim = 1),\
                   torch.cat(c_t, dim = 1),\
                   torch.cat(d_t, dim = 1),\
                   torch.cat(e_t, dim = 1),\
                   torch.cat(h_t, dim = 1) 
        
        return torch.cat(h_t, dim = 1), h_t[-1] # (B,L,M)

class SenseMemAct(nn.Module):
    def __init__(self, sensor_net, actor_net, type = 'BRC', mem_lay = 1, in_sz = 1, mem_sz = 64, decisions = 3, bias = False, ortho = False):
        super().__init__()
        self.sense = sensor_net
        self.act = actor_net
        self.dec = decisions
        self.memsz = mem_sz
        self.orth = ortho
        self.type = type
        if type == 'BRC':
            self.mem = nBRC(in_sz, mem_sz, mem_lay, bias = bias, batch_first = True)
        elif type == 'BEF':
            self.mem = nBEFRC(in_sz, mem_sz, mem_lay, bias = bias, batch_first = True)
        elif type == 'GRU':
            self.mem = nn.GRU(in_sz, mem_sz, mem_lay, bias = bias, batch_first = True)
        else:
            raise NotImplementedError()
        # self.mem = nn.ModuleList([nn.GRU(in_sz, mem_sz, 1, bias = bias, batch_first = True) for _ in range(mem_lay)])
        self.decision = nn.Softmax(dim = -1)
        self.l = nn.CrossEntropyLoss()

    def forward(self, x, debug_mem = False): 
        # print(x, debug_mem)
        # X of the size (Batch, Sequence_lg, Input_sz)
        # Denoted B,L,N
        with torch.no_grad():
            # orthogonal matrix hidden-hidden
            if self.orth:
                u,_,v = torch.linalg.svd(self.mem[0].weight_hh_l0[:self.memsz,:])
                self.mem[0].weight_hh_l0[:self.memsz,:] = u@v

        B, L, N = x.shape        
        # transfer sequence to sensor -> go back to sequence
        inputs = self.sense(x.reshape((-1, N))).reshape((B,L,-1))
        # transfer to memory
        if not debug_mem:
            memory,_ = self.mem(inputs)
            B, L, M = memory.shape
            # transfer sequence output to actions sequence 
            out = self.decision(self.act(memory.reshape((-1, M))).reshape((B,L,self.dec)))
        
        else:
            if self.type != 'GRU':
                out = self.mem(inputs, mem = True)
            else:
                out,_ = self.mem(inputs)

        return out

    def loss(self, x, target):
        # X - (B,L,N) | T - (B,L,O), O = 3 (choices)

        pred = self(x)
        
        mask = (target[:,:,0] != 1)
        not_m = torch.bitwise_not(mask)
        pred_dec, targ_dec = pred[mask], target[mask]
        pred_ndec, targ_ndec = pred[not_m], target[not_m]

        return  (10/30)*self.l(pred_dec, targ_dec) + (20/30)*self.l(pred_ndec, targ_ndec)
        # return self.l(pred, target.transpose(-2,-1))



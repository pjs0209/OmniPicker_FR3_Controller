"""Rigid transforms and nominal CAD kinematics for demo/preview. Units mm/deg."""
import json
import math
from functools import lru_cache
from pathlib import Path


def multiply(a,b):
    return [[sum(a[i][k]*b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def transform(pose):
    x,y,z,r,p,w=pose
    r,p,w=map(math.radians,(r,p,w))
    sr,cr,sp,cp,sw,cw=math.sin(r),math.cos(r),math.sin(p),math.cos(p),math.sin(w),math.cos(w)
    return [[cw*cp,cw*sp*sr-sw*cr,cw*sp*cr+sw*sr,x],
            [sw*cp,sw*sp*sr+cw*cr,sw*sp*cr-cw*sr,y],[-sp,cp*sr,cp*cr,z],[0,0,0,1]]


def pose(matrix):
    p=math.asin(max(-1,min(1,-matrix[2][0])))
    if abs(math.cos(p))>1e-8:
        r=math.atan2(matrix[2][1],matrix[2][2]);w=math.atan2(matrix[1][0],matrix[0][0])
    else:
        r=0;w=math.atan2(-matrix[0][1],matrix[1][1])
    return [matrix[i][3] for i in range(3)]+list(map(math.degrees,(r,p,w)))


def tool_target(reference,delta):
    return pose(multiply(transform(reference),transform(delta)))


@lru_cache(maxsize=2)
def model(name='FR3WMS'):
    return json.loads((Path(__file__).parent/'static/models/models.json').read_text())['arms'][name]


def cad_limits(name='FR3WMS'):
    import xml.etree.ElementTree as ET
    tree=ET.parse(Path(__file__).parent/'static/models'/name/'robot.urdf')
    return [[math.degrees(float(j.find('limit').attrib[k])) for k in ('lower','upper')]
            for j in tree.findall('joint') if j.attrib['type']=='revolute']


def forward_matrix(joints,name='FR3WMS',offset=None):
    result=transform([0]*6);index=0
    for joint in model(name)['joints']:
        origin=joint['origin']
        result=multiply(result,transform([x*1000 for x in origin['xyz']]+[math.degrees(x) for x in origin['rpy']]))
        if joint['type']=='revolute':
            # Both pinned FR3 models use local Z as the physical rotation axis.
            result=multiply(result,transform([0,0,0,0,0,joints[index]]));index+=1
    return multiply(result,transform(offset)) if offset is not None else result


def forward(joints,name='FR3WMS',offset=None): return pose(forward_matrix(joints,name,offset))


def _error(target,current):
    # Orientation error expressed in the base frame; translation scaled to metres.
    rotation=[[sum(target[i][k]*current[j][k] for k in range(3)) for j in range(3)] for i in range(3)]
    return [(target[i][3]-current[i][3])/1000 for i in range(3)]+[
        (rotation[2][1]-rotation[1][2])/2,(rotation[0][2]-rotation[2][0])/2,(rotation[1][0]-rotation[0][1])/2]


def _solve(a,b):
    rows=[list(row)+[v] for row,v in zip(a,b)];n=len(b)
    for i in range(n):
        pivot=max(range(i,n),key=lambda j:abs(rows[j][i]));rows[i],rows[pivot]=rows[pivot],rows[i]
        if abs(rows[i][i])<1e-12: raise ValueError('특이 자세')
        scale=rows[i][i];rows[i]=[x/scale for x in rows[i]]
        for j in range(n):
            if j!=i:
                scale=rows[j][i];rows[j]=[x-scale*y for x,y in zip(rows[j],rows[i])]
    return [row[-1] for row in rows]


def inverse(target_pose,reference,name='FR3WMS',offset=None):
    """Numerical IK for the simulator only. Hardware uses the controller SDK IK."""
    target=transform(target_pose);q=list(reference);bounds=cad_limits(name)
    h=.005
    for _ in range(100):
        current=forward_matrix(q,name,offset);error=_error(target,current)
        if max(abs(v) for v in error[:3])<.00005 and max(abs(v) for v in error[3:])<.0002:return q
        columns=[]
        for i in range(6):
            probe=q[:];probe[i]+=h
            columns.append([v/h for v in _error(forward_matrix(probe,name,offset),current)])
        jacobian=list(map(list,zip(*columns)))
        normal=[[sum(jacobian[k][i]*jacobian[k][j] for k in range(6))+(1e-7 if i==j else 0) for j in range(6)] for i in range(6)]
        rhs=[sum(jacobian[k][i]*error[k] for k in range(6)) for i in range(6)]
        update=_solve(normal,rhs)
        q=[max(lo,min(hi,a+max(-4,min(4,d)))) for a,d,(lo,hi) in zip(q,update,bounds)]
    raise ValueError('목표 자세의 역기구학 해를 찾지 못했습니다.')

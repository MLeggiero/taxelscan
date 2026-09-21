#!/usr/bin/env python3
"""Readable, wired layout for rev3; keeps the existing symbol and sheet UUIDs.

The netlist still defines connectivity. This module defines only the drawing.
Run directly to rearrange the existing sheet, or use gen_schematic.py to rebuild.
"""
import math
import os
import uuid
import sexpdata

from kiutils.schematic import Schematic
from kiutils.symbol import SymbolLib
from kiutils.items.common import Position, Effects, Font, Justify, Stroke, TitleBlock
from kiutils.items.schitems import (Connection, LocalLabel, Text, Junction, NoConnect, PolyLine,
                                   SchematicSymbol, SymbolProjectInstance, SymbolProjectPath)
from kiutils.items.common import Property

import gen_schematic as G


def restore_library_visibility(symbol):
    """kiutils loses KiCad 10's '(hide yes)' flags on stacked pins.

    Recover display flags from the installed library, without changing any pin
    number, position, name or electrical type. This prevents overprinted pin
    numbers on the MCU, USB connector, crystal and power symbols.
    """
    def children(node,key):
        return [v for v in node if isinstance(v,list) and v and str(v[0])==key]
    def hidden(node):
        return any(str(v)=='hide' if not isinstance(v,list) else
                   bool(v and str(v[0])=='hide' and (len(v)==1 or str(v[1])=='yes')) for v in node)
    def raw(lib,name):
        block=G._block(lib,name)
        depth=0; quoted=False; escape=False
        for i,c in enumerate(block):
            if escape: escape=False; continue
            if c=='\\' and quoted: escape=True; continue
            if c=='"': quoted=not quoted
            elif not quoted:
                if c=='(': depth+=1
                elif c==')':
                    depth-=1
                    if depth==0: return sexpdata.loads(block[:i+1])
        raise ValueError('Incomplete library symbol: '+lib+':'+name)
    lib,name=symbol.libId.split(':')
    node=raw(lib,name)
    while children(node,'extends'):
        node=raw(lib,str(children(node,'extends')[0][1]))
    nums=children(node,'pin_numbers'); names=children(node,'pin_names')
    symbol.hidePinNumbers=bool(nums and hidden(nums[0]))
    symbol.pinNamesHide=bool(names and hidden(names[0]))
    flags={}
    for unit in children(node,'symbol'):
        for pin in children(unit,'pin'):
            number=children(pin,'number')
            if number: flags[str(number[0][1])]=hidden(pin)
    for unit in symbol.units:
        for pin in unit.pins:
            if pin.number in flags: pin.hide=flags[pin.number]


def apply_layout(sch, netmap):
    sch.schematicSymbols = [s for s in sch.schematicSymbols
                            if not s.properties[0].value.startswith('#PWRG')]
    sch.paper.paperSize = 'User'
    sch.paper.width, sch.paper.height = 841, 760
    sch.titleBlock = TitleBlock(title='TaxelScan | 32 x 32 sensor interface',
                               revision='3', comments={1: 'Row drive / dual-bank sensing / RP2354A / RS-485',
                                                       2: '3.3 V logic | USB-C data and protected chain power | SWD debug'})
    sch.globalLabels = []
    sch.labels = []
    sch.graphicalItems = []
    sch.shapes = []
    sch.texts = []
    sch.junctions = []
    sch.noConnects = []
    symbols = {(s.properties[0].value, s.unit): s for s in sch.schematicSymbols}
    placed, handled, pins, directions = set(), set(), {}, {}
    wire_segments, dots, labeled = set(), set(), set()
    ground_points = set()
    if not any(s.libId=='power:GND' for s in sch.libSymbols):
        powerlib=SymbolLib.from_file(os.path.join(G.G.SYMDIR,'power.kicad_sym'))
        sch.libSymbols.append(G.flatten({'power':powerlib},'power','GND'))
    for libsymbol in sch.libSymbols:
        restore_library_visibility(libsymbol)

    def pos(x, y, a=None):
        return Position(round(G.snap(x), 5), round(G.snap(y), 5), a)

    def xy(p):
        return (round(p.X, 5), round(p.Y, 5))

    def font(size=1.27, bold=False, side='left'):
        return Effects(font=Font(width=size, height=size, bold=bold),
                       justify=Justify(horizontally=side))

    def note(text, x, y, size=1.52, bold=False):
        sch.texts.append(Text(text=text.replace('\n', r'\n'), position=pos(x, y, 0), effects=font(size, bold), uuid=str(uuid.uuid4())))

    def line(a, b, graphic=False):
        a, b = xy(pos(*a)), xy(pos(*b))
        if a == b:
            return
        key = tuple(sorted([a, b]))
        if not graphic and key in wire_segments:
            return
        if graphic:
            sch.graphicalItems.append(PolyLine(points=[pos(*a), pos(*b)],
                                      stroke=Stroke(width=0.254, type='solid'), uuid=str(uuid.uuid4())))
        else:
            assert a[0] == b[0] or a[1] == b[1], (a, b)
            wire_segments.add(key)
            sch.graphicalItems.append(Connection(points=[pos(*a), pos(*b)],
                                     stroke=Stroke(width=0, type='default'), uuid=str(uuid.uuid4())))

    def route(*points):
        for a, b in zip(points, points[1:]):
            line(a, b)

    def dot(p):
        p = xy(pos(*p))
        if p not in dots:
            sch.junctions.append(Junction(position=pos(*p), uuid=str(uuid.uuid4())))
            dots.add(p)

    def label(net, point, angle=0, side='left'):
        point = xy(pos(*point))
        key = (net, point)
        if key not in labeled:
            sch.labels.append(LocalLabel(text=net, position=pos(*point, angle),
                                         effects=font(side=side), uuid=str(uuid.uuid4())))
            labeled.add(key)

    def ground(point):
        point=xy(pos(*point))
        if point in ground_points:
            return
        ground_points.add(point)
        ref='#PWRG%03d'%len(ground_points)
        uid=str(uuid.uuid5(uuid.UUID(sch.uuid),'ground:'+str(point)))
        sym=SchematicSymbol(libraryNickname='power',entryName='GND',unit=1,
                            position=pos(*point,0),uuid=uid,inBom=False,onBoard=True)
        sym.properties=[Property(key='Reference',value=ref,position=pos(*point,0),effects=Effects(hide=True)),
                        Property(key='Value',value='GND',position=pos(point[0],point[1]+5.08,0),
                                 effects=font(side=None))]
        sym.instances=[SymbolProjectInstance(name='rev3',paths=[SymbolProjectPath(
                       sheetInstancePath='/'+sch.uuid,reference=ref,unit=1)])]
        sch.schematicSymbols.append(sym)

    def panel(x1, y1, x2, y2, title, subtitle):
        for a,b in [((x1,y1),(x2,y1)),((x2,y1),(x2,y2)),((x2,y2),(x1,y2)),((x1,y2),(x1,y1))]:
            line(a,b,True)
        note(title,x1+5,y1+7,2.54,True)
        note(subtitle,x1+5,y1+14,1.52)
        line((x1+5,y1+20),(x2-5,y1+20),True)

    def place(ref, x, y, angle=0, unit=1, mirror=None, fields=None):
        s = symbols[(ref,unit)]
        # Drawing calls use 270 for a passive whose pin 1 should be on the left.
        # KiCad rotates in library coordinates; account for the inverted page Y.
        if ref.startswith(('R','L')) and angle in (90,270):
            angle=360-angle
        placed.add((ref,unit))
        s.position, s.mirror = pos(x,y,angle), mirror
        s.fieldsAutoplaced = False
        x,y = xy(s.position)
        pp = G.units_of(*s.libId.split(':'))
        raw = pp.get(unit, []) + pp.get(0, [])
        theta = math.radians(angle)
        allpoints = []
        for number, px, py, pa in raw:
            if mirror == 'y':
                px, pa = -px, 180-pa
            if mirror == 'x':
                py, pa = -py, -pa
            qx = px*math.cos(theta)-py*math.sin(theta)
            qy = px*math.sin(theta)+py*math.cos(theta)
            p = xy(pos(x+qx,y-qy))
            pins[(ref,number)] = p
            # Pin angle points into the body, so the wire extends in reverse.
            phi = math.radians(pa+angle)
            directions[(ref,number)] = (-round(math.cos(phi)),round(math.sin(phi)))
            allpoints.append(p)
        for prop in s.properties:
            prop.position = pos(x,y,0)
            prop.effects = font()
            prop.effects.hide = prop.key not in ('Reference','Value') or ref.startswith('#')
            prop.showName = False
        xs,ys = zip(*allpoints)
        if fields:
            fx,fy,side = fields
        elif ref.startswith(('R','C','L')) and angle in (0,180):
            fx,fy,side = x+3.81,y-1.27,'left'
        elif ref.startswith(('R','C','L','D','JP','SW','Y')):
            fx,fy,side = x,y-7.62,'center'
        elif ref == 'U7' and unit != 3:
            fx,fy,side = x,y-10.16,'center'
        else:
            fx,fy,side = x,min(ys)-7.62,'center'
        for prop in s.properties:
            if prop.key in ('Reference','Value'):
                prop.position = pos(fx,fy+(2.54 if prop.key=='Value' else 0),90 if angle in (90,270) else 0)
                rendered_side=side
                if (mirror=='y' or angle==180) and side in ('left','right'):
                    rendered_side='right' if side=='left' else 'left'
                prop.effects.justify = Justify(horizontally=rendered_side if rendered_side!='center' else None)
        return s

    def p(ref, pin):
        return pins[(ref,str(pin))]

    def handled_pin(ref,pin):
        handled.add((ref,str(pin)))

    def join(net, endpoints, waypoints=(), name_at=None):
        points = [p(*ep) for ep in endpoints]
        for ep in endpoints:
            assert netmap[ep[0]][str(ep[1])] == net, (net,ep)
            handled_pin(*ep)
        route(points[0],*waypoints,points[-1])
        if name_at:
            label(net,name_at)

    def stub(ref,pin,length=7.62):
        pin = str(pin)
        key=(ref,pin)
        if key in handled:
            return
        handled.add(key)
        start=pins[key]
        net=netmap.get(ref,{}).get(pin)
        if net is None:
            if not any(xy(n.position)==start for n in sch.noConnects):
                sch.noConnects.append(NoConnect(position=pos(*start),uuid=str(uuid.uuid4())))
            return
        dx,dy=directions[key]
        end=xy(pos(start[0]+dx*length,start[1]+dy*length))
        line(start,end)
        if dy:
            if net=='GND' and dy>0:
                ground(end)
            else:
                label(net,end,90 if dy<0 else 90,'left' if dy<0 else 'right')
        else:
            label(net,end,0,'left' if dx>0 else 'right')

    def capbank(refs,x,y,rail,step=22.86):
        top,bottom=G.snap(y-8.89),G.snap(y+8.89)
        for i,ref in enumerate(refs):
            xx=G.snap(x+i*step)
            place(ref,xx,y)
            for pin,yy in [(1,top),(2,bottom)]:
                assert netmap[ref][str(pin)] == (rail if pin==1 else 'GND')
                line(p(ref,pin),(xx,yy));handled_pin(ref,pin)
        right=G.snap(x+(len(refs)-1)*step)
        if len(refs)>1:
            line((x,top),(right,top));line((x,bottom),(right,bottom))
            for i in range(1,len(refs)-1):
                dot((x+i*step,top));dot((x+i*step,bottom))
        label(rail,(x,top))
        line((x,bottom),(x,bottom+2.54));ground((x,bottom+2.54))

    note('TAXELSCAN  /  REV 3',13,17,4,True)
    note('32 x 32 matrix  >  two sense banks  >  16-bit ADC  >  RP2354A  >  RS-485 harness',190,17,2.0)
    panel(12,32,289,274,'01  ROW EXCITATION','MCU serial stream > four latched drivers > 32-way row FFC')
    panel(300,32,577,274,'02  COLUMN SENSING + CONVERSION','32 columns > two 16:1 muxes > gain of 6 > filtered ADC inputs')
    panel(588,32,829,274,'03  SCAN CONTROLLER','RP2354A / clock / core supply / local bypass')
    panel(12,286,289,495,'04  POWER + RAIL MONITOR','Harness or USB power > diode OR > local 3.3 V buck')
    panel(300,286,577,495,'05  COMMUNICATIONS + BOARD ADDRESS','Half-duplex data pair and dedicated receive-only sync pair')
    panel(588,286,829,495,'06  USB + PROGRAMMING','USB-C data / configuration straps / SWD / BOOTSEL')

    # Row registers. Their repeated parallel ports use consistent pin stubs.
    for ref,x,y in [('U1',59.69,86.36),('U2',135.89,86.36),('U3',59.69,158.75),('U4',135.89,158.75)]:
        place(ref,x,y,fields=(x+5.08,y-26.67,'left'))
        note({'U1':'Rows 0-7','U2':'Rows 8-15','U3':'Rows 16-23','U4':'Rows 24-31'}[ref],x+7.62,y+31.75,1.52,True)
    for first,second,net in [('U1','U2','SR_CHAIN_1'),('U3','U4','SR_CHAIN_3')]:
        a,b=p(first,9),p(second,14)
        join(net,[(first,9),(second,14)],[(102.87,a[1]),(102.87,b[1])],(77.47,a[1]))
    join('SR_CHAIN_2',[('U2',9),('U3',14)],
         [(194.31,99.06),(194.31,194.31),(25.4,194.31),(25.4,148.59)],(165.1,99.06))
    place('J1',251.46,115.57)
    note('ROW FFC',231,60,1.78,True)
    note('Pin n = ROW_(n-1)\nMounting pad = GND',219,174)
    note('Serial order: U1 > U2 > U3 > U4\nOutputs enabled; unselected rows held LOW.',25,201.93)
    place('R3',85.09,216.0,270)
    place('R4',190.5,216.0,270)
    note('Clock / latch damping',25,228,1.52,True)
    capbank(['C1','C2','C3','C4'],35.56,253.0,'ROW_VCC')
    note('U1-U4 bypass',34,235,1.27)
    place('R5',193.04,248.92,270)
    note('Excitation rail link',168,234,1.52,True)

    # Column connector and mirrored muxes: columns enter from the left.
    place('J2',327.66,118.11,mirror='y')
    note('COLUMN FFC',309,62,1.78,True)
    note('Pin n = COL_(n-1)\nMounting pad = GND',309,185.42,1.27)
    for bank,mux,unit,y,rpd,rf,rg,rs,cap,decap in [
        ('A','U5',1,99.06,'R1','R6','R7','R21','C31','C5'),
        ('B','U6',2,181.61,'R2','R8','R9','R22','C32','C6')]:
        x=396.24
        place(mux,x,y,mirror='y',fields=(x+5.08,y-35.56,'left'))
        oy=G.snap(y-10.16)
        place('U7',472.44,oy,unit=unit)
        place(rpd,438.15,oy+17.78)
        place(rf,472.44,oy+27.94,270)
        place(rg,450.85,oy+38.10)
        place(rs,515.62,oy,270)
        place(cap,541.02,oy+17.78)
        inpin=3 if unit==1 else 5
        negpin=2 if unit==1 else 6
        outpin=1 if unit==1 else 7
        sx,sy=p(mux,1);ix,iy=p('U7',inpin)
        join('SENSE_'+bank,[(mux,1),('U7',inpin)],[(438.15,sy),(438.15,iy)],(419.1,sy))
        route(p(rpd,1),(438.15,iy));handled_pin(rpd,1);dot((438.15,iy))
        nx,ny=p('U7',negpin);fy=p(rf,1)[1]
        join('GAIN_'+bank,[('U7',negpin),(rf,1)],[(450.85,ny),(450.85,fy)])
        route(p(rg,1),(450.85,fy));handled_pin(rg,1);dot((450.85,fy));label('GAIN_'+bank,(450.85,fy))
        out=p('U7',outpin);tap=(495.30,oy)
        join('AMP_'+bank,[('U7',outpin),(rs,1)],name_at=tap)
        route(p(rf,2),(495.30,fy),tap);handled_pin(rf,2);dot(tap)
        route(p(rs,2),(541.02,oy),p(cap,1));handled_pin(rs,2);handled_pin(cap,1)
        label('ADC_'+bank,(541.02,oy));dot((541.02,oy))
        capbank([decap],422.91,y-35.56,'+3.3V')
    note('R1/R2: 10k pulldown\nRf/Rg: 10k / 2k  (G = 6)',307,199,1.27)
    note('51R / 1nF at each ADC input\nSeries resistors outside feedback',472,211,1.27)
    place('U8',505.46,241.30,fields=(524.51,223.52,'left'))
    place('R10',355.6,228.6,270)
    capbank(['C10','C26'],373.38,250.19,'VREF',22.86)
    # Reference resistor explicitly feeds its reservoir.
    join('VREF',[('R10',2),('C10',1)],[(373.38,228.6)],(373.38,228.6))
    # capbank already draws the capacitor rail; this branch joins it at its endpoint.
    note('VREF tracks ROW_VCC',309,218,1.27,True)
    place('U7',434.34,243.84,unit=3,fields=(438.15,231.14,'left'))
    capbank(['C7'],454.66,251.46,'+3.3V')
    capbank(['C8','C11'],538.48,254.0,'+3.3V',21.59)

    # MCU with consistently oriented labels, no piled-up power labels.
    place('U9',704.85,124.46,fields=(727.71,72.39,'left'))
    note('ROW_*     > section 01\nMUX_*, ADC_* > section 02\nBUS_*, SYNC_* > section 05\nUSB_*, SWD* > section 06',602,90,1.52)
    note('Unused GPIO / stacked-flash pins\nare marked with no-connects.',602,144,1.27)
    place('Y1',624.84,199.39)
    capbank(['C20'],612.14,217.17,'XIN')
    capbank(['C21'],645.16,217.17,'XOUT')
    join('XIN',[('Y1',1),('C20',1)],[(612.14,199.39)])
    join('XOUT',[('Y1',3),('C21',1)],[(645.16,199.39)])
    place('R36',668.02,199.39)
    note('R36: 1k XOUT series, limits crystal drive',655,183,1.27)
    note('12 MHz / C0G load capacitors',602,183,1.27)
    place('L1',694.69,195.58,90)     # pin 1 (the polarity dot) on the VCORE side
    capbank(['C19','C24','C25','C35'],718.82,215.9,'VCORE',20.32)
    place('R35',805.18,190.5,270)
    capbank(['C43','C16'],790.0,215.9,'VREG_AVDD',20.32)
    note('VREG_AVDD: 33R / 4.7uF filter',783,240,1.27)
    join('VCORE',[('L1',1),('C19',1)],[(718.82,195.58)],(718.82,195.58))
    note('Core regulator / DVDD bypass',737,187,1.27)
    capbank(['C12','C13','C14','C15','C28','C29','C33','C34','C44'],601.98,254.0,'+3.3V',24.13)
    note('MCU 3.3 V bypass',603,239,1.27,True)

    # Input OR and buck are drawn as complete circuits.
    place('D1',55.88,330.2,180)
    place('D2',55.88,358.14,180)
    join('+5V',[('D1',1),('D2',1)],[(80.01,330.2),(80.01,358.14)],(80.01,330.2))
    capbank(['C39'],31.75,347.98,'+5V_BUS')
    capbank(['C37','C38'],31.75,393.7,'+5V_USB',25.4)
    place('U12',129.54,342.9,fields=(119.38,325.12,'left'))
    place('L2',172.72,340.36,270)
    place('R18',193.04,358.14)
    place('R19',193.04,378.46)
    capbank(['C22','C27'],102.87,393.7,'+5V',25.4)
    capbank(['C23','C9'],223.52,365.76,'+3.3V',26.67)
    join('+5V',[('U12',4),('U12',1)],[(113.03,340.36),(113.03,342.90)],(100.33,340.36))
    line((100.33,340.36),(113.03,340.36))
    route((80.01,330.2),(100.33,330.2),(100.33,340.36));dot((80.01,330.2))
    join('SW_NODE',[('U12',3),('L2',1)],name_at=(149.86,340.36))
    join('+3.3V',[('L2',2),('R18',1)],[(193.04,340.36)],(193.04,340.36))
    line((193.04,340.36),(223.52,340.36));line((223.52,340.36),p('C23',1));dot((193.04,340.36))
    join('FB',[('R18',2),('R19',1)],name_at=(193.04,368.3))
    route(p('U12',5),(153.67,342.9),(153.67,368.3),(193.04,368.3));handled_pin('U12',5);dot((193.04,368.3))
    note('3.3 V buck output\nFeedback: 180k / 40.2k',207,393,1.52)
    place('R15',54.61,440.69)
    place('R16',54.61,462.28)
    join('RAIL_MON',[('R15',2),('R16',1)],name_at=(54.61,450.85))
    capbank(['C45'],92.71,460.0,'RAIL_MON')
    note('5 V rail monitor\n100k / 47k divider to MCU',27,421,1.52)
    place('R26',137.16,441.96,270)
    capbank(['C36'],167.64,460.0,'ADC_AVDD')
    join('ADC_AVDD',[('R26',2),('C36',1)],[(167.64,441.96)],(167.64,441.96))
    note('MCU analog supply filter',117,427,1.52)
    note('ROW_VCC link / VREF filter:\nsee sections 01 and 02.\nAll returns use GND.',211,442,1.52)

    # RS-485 transceivers, line termination, and through-wired connectors.
    for ref,res,cap,y,a,b in [('U10','R11','C17',340.36,'BUS_P','BUS_N'),
                            ('U11','R12','C18',430.53,'SYNC_P','SYNC_N')]:
        place(ref,353.06,y,fields=(358.14,y-26.67,'left'))
        place(res,401.32,y+2.54)
        ax,ay=p(ref,6);bx,by=p(ref,7)
        join(a,[(ref,6),(res,1)],[(401.32,ay)],(379.73,ay))
        join(b,[(ref,7),(res,2)],[(382.27,by),(382.27,y+13.97),(401.32,y+13.97)],(382.27,y+13.97))
        capbank([cap],322.58,y+35.56,'+3.3V')
        if ref=='U10':
            join('BUS_DE',[(ref,2),(ref,3)],[(335.28,y-2.54),(335.28,y)],(327.66,y-2.54))
            line((327.66,y-2.54),(335.28,y-2.54))
        else:
            gp=(335.28,y+22.86)
            for pin in (2,4):
                pt=p(ref,pin);route(pt,(335.28,pt[1]),gp);handled_pin(ref,pin)
            ground(gp)
            # DE: the master's frame-start driver, held off by R37 on every board
            dx,dy=p(ref,3)
            end=(dx-(dx-335.28)+2.54,dy)
            line((dx,dy),end);handled_pin(ref,3);label('SYNC_DE',end)
            place('R37',312.42,y+10.16)
            r1=p('R37',1);r2=p('R37',2)
            label('SYNC_DE',r1);handled_pin('R37',1)
            g37=(r2[0],r2[1]+2.54);line(r2,g37);handled_pin('R37',2);ground(g37)
    note('DATA / half duplex',342,388,1.52,True)
    note('SYNC / master drives DE (GPIO13)\nR37 keeps other boards receive-only',342,482,1.52,True)
    note('R11 / R12: fit 120R only\nat the two harness ends.',442,384,1.52,True)
    place('J3',454.66,342.9,mirror='y')
    place('J4',548.64,342.9)
    for pin in range(1,7):
        net=netmap['J3'][str(pin)]
        join(net,[('J3',pin),('J4',pin)],name_at=(491.49,p('J3',pin)[1]))
    note('HARNESS IN',438,315,1.52,True)
    note('HARNESS OUT',526,315,1.52,True)
    note('1  +5V    2  GND    3/4  DATA P/N    5/6  SYNC P/N',436,374,1.27)
    for ref,x in [('JP1',463.55),('JP2',503.0),('JP3',543.56)]:
        place(ref,x,421.64)
    note('Board address straps: ADDR0 / ADDR1 / ADDR2\nClosing a strap connects that input to GND.',441,442,1.52)
    place('R17',471.17,477.52,270)
    place('D3',520.7,477.52,180)
    join('LED_A',[('R17',2),('D3',2)],name_at=(491.49,477.52))
    note('Status indicator',441,460,1.52,True)

    # USB pin pairs are visibly combined before the series resistors.
    place('J5',617.22,347.98,fields=(608.33,317.5,'left'))
    place('R24',687.07,345.44,90)
    place('R23',687.07,361.95,90)
    for ca,cb,res,xx in [('A7','B7','R24',642.62),('A6','B6','R23',654.05)]:
        net=netmap['J5'][ca]
        a,b=p('J5',ca),p('J5',cb);rp=p(res,2)
        route(a,(xx,a[1]),(xx,rp[1]),rp)
        route(b,(xx,b[1]),(xx,a[1]));handled_pin('J5',ca);handled_pin('J5',cb);handled_pin(res,2)
        dot((xx,a[1]));label(net,(xx,rp[1]))
    note('CC1 / CC2 terminate inside U13 (section 07).\nDo not fit external 5.1k pulldowns.',638,390,1.52,True)
    # ESD array on the connector side of the series resistors; its pins carry
    # the connector-side nets and VBUS, and get labelled stubs like any pin.
    place('D5',748.0,419.1,fields=(760.0,404.0,'left'))
    note('D5: USB ESD, D+/D-/VBUS at the connector',714,400,1.27)
    place('J6',780.0,340.36)
    note('SWD DEBUG',758,317,1.52,True)
    place('R20',781.05,394.97,180)
    note('RUN pull-up',761,378,1.52,True)
    place('R25',674.37,459.74,270)
    place('SW1',732.79,459.74)
    join('BOOTSEL_SW',[('R25',2),('SW1',1)],name_at=(698.5,459.74))
    note('BOOTSEL: series resistor limits the flash-CS stub',611,439,1.52)
    note('USB series resistors at MCU end\nUSB connector shell and all returns: GND',611,478,1.52)

    panel(12,507,829,675,'07  USB-C POWER TO THE HARNESS',
          'Computer port current detection > firmware authorization > current limit > reverse barrier > +5V_BUS')
    place('U13',88.9,567.69)
    place('R32',187.96,558.8)
    place('R33',241.3,558.8)
    place('R34',172.72,618.49,270)
    capbank(['C40','C41'],35.56,629.92,'+3.3V',30.48)
    note('UFP / sink only; ADDR left open selects GPIO mode.\nLAI variant required: OUT1/OUT2 track current changes.',22,653,1.52)
    place('U14',353.06,561.34)
    place('D4',411.48,557.53,180)
    join('USB_BUS_SW',[('U14',6),('D4',2)],name_at=(377.19,557.53))
    place('C42',302.26,624.84)
    # Hot-plug damper: a series RC across +5V_USB, drawn as the pair it is.
    place('R38',241.3,605.79)
    place('C46',241.3,636.27)
    join('USB_SNUB',[('R38',2),('C46',1)],name_at=(241.3,621.03))
    note('R38 / C46: 1R + 10uF damps the\nhot-plug ring on +5V_USB',196,570,1.27)
    place('R30',347.98,624.84)
    place('R31',406.4,624.84)
    place('R27',464.82,594.36)
    place('R28',518.16,594.36)
    place('Q1',518.16,634.99999)
    place('R29',584.2,632.46)
    a,b=p('R27',1),p('R28',1)
    join('USB_ILIM',[('R27',1),('R28',1)])
    q=p('U14',5)
    route(q,(439.42,q[1]),(439.42,a[1]),a)
    handled_pin('U14',5);dot(a);label('USB_ILIM',a)
    q=p('Q1',3);a=p('R28',2)
    join('USB_ILIM_LOW',[('R28',2),('Q1',3)],[(a[0],619.76),(q[0],619.76)])
    label('USB_ILIM_LOW',(a[0],619.76))
    note('LOW limit: R27 only, about 0.32 A nominal\nHIGH limit: R27 || R28, about 0.63 A nominal\nD4 blocks an externally powered harness from USB.',300,653,1.52)
    note('POWER POLICY  (GPIO 12 / 13 / 14 / 15 / 23)',627,543,1.78,True)
    note('Reset / no USB / fault: feed OFF.\nDefault USB current: enable LOW only after\nUSB configuration grants 500 mA.\nCC advertises 1.5 A or 3 A: HIGH permitted.\nFewer boards draw less; limits do not force current.\n\nFirmware must service CC changes promptly,\ndisable on default-current suspend/reset,\nand latch faults OFF until an explicit retry.\nSee USB_POWER.md for budgets and bring-up.',627,556,1.52)

    # Keep the original ERC flags, grouped and clearly identified.
    note('POWER-NET DRIVE DECLARATIONS',18,690,1.78,True)
    note('Reading the sheet',405,690,1.78,True)
    note('Matching net labels connect between sections. A dot marks a branch; an X marks an unused pin.\nBypass capacitors are grouped by supply. USB attach current and the complete eight-board\nstartup / operating budget require measurements; schematic checks cannot establish them.',405,699,1.52)
    for i,net in enumerate(G.PWR_FLAG_NETS):
        ref='#FLG%02d'%i
        netmap[ref]={'1':net}
        place(ref,27.94+i*36.83,711.2)
    note('Sensor path: ROW_n > mat element > COL_n > SENSE_A/B > AMP_A/B > ADC_A/B',18,737,2.0,True)
    note('R1/R2 and gain-network values remain fit-on-test values from the existing design.',18,747,1.52)

    assert placed==set(symbols), 'Unplaced symbols: '+str(set(symbols)-placed)
    for ref,pa,pb in [('U8','4','5'),('U9','47','61')]:
        a,b=p(ref,pa),p(ref,pb)
        gy=max(a[1],b[1])+5.08
        route(a,(a[0],gy),(b[0],gy),b)
        line((b[0],gy),(b[0],gy+2.54));ground((b[0],gy+2.54))
        handled_pin(ref,pa);handled_pin(ref,pb)
    # Attach outward-facing stubs only to pins not consumed by local circuits.
    for ref,pin in pins:
        stub(ref,pin,5.08 if ref=='U9' else 7.62)
    # Two grounded pins on the ADC/MCU are joined once, with a common label.
    # Deduplicate overlapping stubs for internally stacked supply pins as well.
    return sch


if __name__=='__main__':
    schematic=Schematic.from_file(G.OUT)
    apply_layout(schematic,G.load_net()).to_file(G.OUT)
    print('Updated rev3 schematic layout; sheet and symbol UUIDs retained.')

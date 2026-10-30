/* Dependency-free SVG tree renderer. Data is inserted as text, never HTML. */
function drawTree(host, value, plan=false, guide=null) {
  let relationNumber=0;
  const ns='http://www.w3.org/2000/svg';
  function el(tag, attrs={}, text){const e=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,v);if(text!==undefined)e.textContent=text;return e;}
  function node(v,label=''){
    if(v===null||typeof v!=='object')return {title:label||'Value',sub:String(v),raw:v,children:[]};
    if(Array.isArray(v))return {title:label,sub:v.length+' items',raw:v,children:v.map(x=>node(x))};
    const title=v.type||label||'Node';
    const sub=[v.operator,v.name,v.table,v.value!==undefined?String(v.value):null,v.estimated_rows!==undefined?'≈ '+v.estimated_rows+' rows':null].filter(x=>x!==null&&x!==undefined).join(' · ');
    let children=[];
    if(plan){for(const k of ['input','left','right'])if(v[k])children.push(node(v[k],k));}
    else for(const [k,x]of Object.entries(v)){if(k==='type'||x===null)continue;if(typeof x==='object'){if(Array.isArray(x)&&!x.length)continue;children.push(node(x,k));}else if(!['name','operator','table','value'].includes(k)&&x!==false)children.push(node(x,k));}
    const step=plan&&guide?guide.steps[relationNumber++]:null;
    return {title:step?step.relation+' · '+title:title,sub,raw:v,children,step};
  }
  const root=node(value),nw=166,nh=59,gap=22,dy=106;let maxDepth=0;
  function measure(n,d=0){maxDepth=Math.max(maxDepth,d);n.width=Math.max(nw,n.children.reduce((a,c)=>a+measure(c,d+1),0)+Math.max(0,n.children.length-1)*gap);return n.width;}measure(root);
  function place(n,left,d){n.x=left+n.width/2;n.y=35+d*dy;let x=left+(n.width-(n.children.reduce((a,c)=>a+c.width,0)+Math.max(0,n.children.length-1)*gap))/2;for(const c of n.children){place(c,x,d+1);x+=c.width+gap;}}place(root,30,0);
  const toolbar=document.createElement('div');toolbar.className='tree-toolbar';host.append(toolbar);
  const frame=document.createElement('div');frame.className='tree-frame';host.append(frame);
  const svg=el('svg',{role:'img','aria-label':plan?'Relational query plan tree':'Abstract syntax tree'});svg.append(el('title',{},plan?'Relational query plan':'Abstract syntax tree'));frame.append(svg);
  const g=el('g');svg.append(g);const detail=document.createElement('pre');detail.className='tree-detail';detail.textContent='Select a node to inspect it. Drag to pan; use + / − to zoom.';host.append(detail);
  function render(n,depth=0){for(const c of n.children){g.append(el('path',{d:`M ${n.x} ${n.y+nh} C ${n.x} ${n.y+85}, ${c.x} ${c.y-25}, ${c.x} ${c.y}`,fill:'none',stroke:'#c1aedb','stroke-width':1.6}));render(c,depth+1);}
    const item=el('g',{class:'tree-node',tabindex:'0',role:'button','aria-label':n.title+' '+n.sub});item.append(el('rect',{x:n.x-nw/2,y:n.y,width:nw,height:nh,rx:10,fill:['#e9dfff','#d7f4df','#ffe1cc','#e1ecff'][depth%4],stroke:'#d5c7e6'}));item.append(el('text',{x:n.x,y:n.y+24,'text-anchor':'middle',fill:'#29243c','font-family':'Segoe UI, Arial','font-size':12,'font-weight':600},n.title.length>23?n.title.slice(0,21)+'…':n.title));item.append(el('text',{x:n.x,y:n.y+43,'text-anchor':'middle',fill:'#71647e','font-family':'Consolas, monospace','font-size':10},n.sub.length>25?n.sub.slice(0,23)+'…':n.sub));const inspect=()=>detail.textContent=(n.step?n.step.relation+" · "+n.step.operator+"\n"+n.step.explanation+"\n\nPlan properties:\n":"")+JSON.stringify(n.raw,null,2);item.onclick=inspect;item.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();inspect();}};g.append(item);
  }render(root);
  const whole={x:0,y:0,w:root.width+60,h:maxDepth*dy+130};let view={...whole};const update=()=>svg.setAttribute('viewBox',`${view.x} ${view.y} ${view.w} ${view.h}`);update();
  for(const [label,fn]of [['Fit',()=>{view={...whole};update();}],['+',()=>zoom(.75)],['−',()=>zoom(1.3)],['↓ SVG',()=>{const b=new Blob([new XMLSerializer().serializeToString(svg)],{type:'image/svg+xml'});saveBlob(b,plan?'query-plan.svg':'ast.svg');}]]){const b=document.createElement('button');b.textContent=label;b.onclick=fn;toolbar.append(b);}
  function zoom(f){const w=view.w*f,h=view.h*f;if(w<150||w>whole.w*8)return;view={x:view.x+(view.w-w)/2,y:view.y+(view.h-h)/2,w,h};update();}
  let drag;svg.onpointerdown=e=>{if(e.target.closest('.tree-node'))return;drag={x:e.clientX,y:e.clientY,v:{...view}};svg.setPointerCapture(e.pointerId);};svg.onpointermove=e=>{if(!drag)return;const scale=Math.max(view.w/frame.clientWidth,view.h/frame.clientHeight);view.x=drag.v.x-(e.clientX-drag.x)*scale;view.y=drag.v.y-(e.clientY-drag.y)*scale;update();};svg.onpointerup=svg.onpointercancel=()=>drag=null;
}
function saveBlob(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}

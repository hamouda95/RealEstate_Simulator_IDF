const APPELS=[[0,.05],[3,.35],[9,.70],[18,.95]];
const pmt=(c,r,n)=>c<=0||n<=0?0:(r===0?c/n:c*r/(1-Math.pow(1+r,-n)));
const TVA=.2,BAREME=[[6500,.0387],[17000,.01596],[60000,.01064],[Infinity,.00799]],DROITS={neuf:.00715,ancien:.0580665};
function emoluments(b){let t=0,lo=0;for(const[hi,tx]of BAREME){if(b>lo)t+=(Math.min(b,hi)-lo)*tx;lo=hi}return t}
function fraisNotaire(prix,etat){
 if(prix<=0)return{emoluments:0,tva:0,droits:0,csi:0,debours:0,total:0};
 const base=etat==="neuf"?prix/(1+TVA):prix,em=emoluments(base);
 const d={emoluments:em,tva:em*TVA,droits:base*DROITS[etat],csi:Math.max(15,prix*.001),debours:1200};
 d.total=d.emoluments+d.tva+d.droits+d.csi+d.debours;return d}
function fraisInitiaux(p){
 const notaire=fraisNotaire(p.prix,p.etat).total,g=p.garantie/100;
 const b=p.prix+notaire+p.frais_dossier+p.courtage-p.apport-p.al,garantie=b>0?g*b/(1-g):0;
 return{notaire,dossier:p.frais_dossier,garantie,courtage:p.courtage,total:notaire+p.frais_dossier+garantie+p.courtage}}
function taeg(rows,nb,total,fc){
 if(total<=0)return 0;
 const flux=new Array(rows.length+1).fill(0);let deja=0;
 for(let m=1;m<=nb;m++){const x=rows[m-1],deb=x.balP+x.balAl+x.balPtz;flux[m-1]+=deb-deja;deja=deb}
 flux[nb]+=total-deja;flux[0]-=fc;rows.forEach((x,j)=>flux[j+1]-=x.total);
 const van=i=>{let v=0,f=1;for(const c of flux){v+=c*f;f/=1+i}return v};
 let lo=-.0099,hi=.05;if(van(lo)>0||van(hi)<0)return null;
 for(let k=0;k<80;k++){const mi=(lo+hi)/2;if(van(mi)<0)lo=mi;else hi=mi}
 return(Math.pow(1+(lo+hi)/2,12)-1)*100}
function taxeFonciereAnnee(p,an){
 const yl=Number((p.etat==="neuf"?p.date_liv:p.date_achat).slice(0,4));
 if(an<=yl)return 0;
 const exo=p.etat==="neuf"&&an<=yl+2?p.tf_exo/100:0;return p.tf*(1-exo)+p.teom}
const moisEntre=(a,b)=>{const[x,y]=a.split("-").map(Number),[u,v]=b.split("-").map(Number);return (u-x)*12+(v-y)};
function libelle(k,p){
 if(k===1)return "Livraison : début des remboursements";
 const e=[];
 if(p.ptz_dif>0&&k===p.ptz_dif*12+1)e.push("début du remboursement du PTZ");
 if(k===p.ptz_dur*12+1)e.push("fin du PTZ");
 if(k===p.duree_al*12+1)e.push("fin du prêt Action Logement");
 if(k===p.duree*12+1)e.push("fin du prêt principal");
 return e.length?e.join(", ").replace(/^./,c=>c.toUpperCase()):"Mensualité modifiée";
}
function periodes(rows,nb,p){
 let segs=[];
 rows.forEach((x,i)=>{const t=x.total,s=segs[segs.length-1];
  if(s&&s.phase===x.phase&&Math.abs(t-s.dernier)<1){s.fin=i;s.dernier=t;s.mini=Math.min(s.mini,t);s.maxi=Math.max(s.maxi,t)}
  else segs.push({phase:x.phase,debut:i,fin:i,dernier:t,mini:t,maxi:t,label:x.phase===1?"Chantier":libelle(x.m-nb,p)})});
 const ch=segs.filter(s=>s.phase===1);
 if(ch.length>6)segs=[{phase:1,debut:ch[0].debut,fin:ch[ch.length-1].fin,label:"Chantier",mini:Math.min(...ch.map(s=>s.mini)),maxi:Math.max(...ch.map(s=>s.maxi))},...segs.filter(s=>s.phase===2)];
 else if(ch.length>1)ch.forEach((s,j)=>s.label="Chantier – palier "+(j+1));
 const n=rows.length;
 return segs.map(s=>({label:s.label,phase:s.phase,debut:s.debut,mois:s.fin-s.debut+1,mini:+s.mini.toFixed(2),maxi:+s.maxi.toFixed(2),restant:n-s.debut}));
}
function synthese(p){
 const nb=Math.max(0,moisEntre(p.date_achat,p.date_liv));
 const fi=fraisInitiaux(p),frais=fi.total,princ=p.prix+frais-p.apport-p.ptz-p.al,total=princ+p.ptz+p.al;
 const r=p.taux/1200,rAl=p.taux_al/1200,ia=p.ass/1200;
 const mP=pmt(princ,r,p.duree*12),mAl=pmt(p.al,rAl,p.duree_al*12);
 const mZ=p.ptz/Math.max(1,(p.ptz_dur-p.ptz_dif)*12);
 const rows=[];
 for(let m=1;m<=nb;m++){
  let pct=0;
  if(p.fonds==="tout")pct=1;else if(p.fonds==="lin")pct=m/nb;
  else APPELS.forEach(([k,v])=>{if(k<m)pct=v});
  const deb=Math.min(Math.max(p.prix*pct+frais-p.apport,0),total),part=total>0?deb/total:0;
  rows.push({m,phase:1,interet:princ*part*r+p.al*part*rAl,princ:0,al:0,ptz:0,ass:deb*ia,balP:princ*part,balAl:p.al*part,balPtz:p.ptz*part});
 }
 const hor=Math.round(Math.max(p.duree,p.duree_al,p.ptz_dur)*12),fin=Math.max(p.duree,p.duree_al)*12;
 let bP=princ,bA=p.al,bZ=p.ptz;
 for(let k=1;k<=hor;k++){
  let iP=0,cP=0,iA=0,cA=0,cZ=0;
  if(k<=p.duree*12&&bP>0.005){iP=bP*r;cP=Math.min(mP-iP,bP);bP-=cP}
  if(k<=p.duree_al*12&&bA>0.005){iA=bA*rAl;cA=Math.min(mAl-iA,bA);bA-=cA}
  if(k>p.ptz_dif*12&&k<=p.ptz_dur*12&&bZ>0.005){cZ=Math.min(mZ,bZ);bZ-=cZ}
  rows.push({m:nb+k,phase:2,interet:iP+iA,princ:cP,al:cA,ptz:cZ,ass:k<=fin?total*ia:0,balP:bP,balAl:bA,balPtz:bZ});
 }
 rows.forEach(x=>{x.total=x.interet+x.princ+x.al+x.ptz+x.ass;x.bal=x.balP+x.balAl+x.balPtz});
 const ph2=rows.filter(x=>x.phase===2);
 let iz=-1;rows.forEach((x,i)=>{if(x.ptz>0)iz=i});
 const apres=iz>=0&&iz+1<rows.length?rows[iz+1].total:null;
 const sum=(a,k)=>a.reduce((s,x)=>s+x[k],0);
 const interets=sum(rows,"interet"),assurance=sum(rows,"ass");
 const alertes=[];
 if(princ<0)alertes.push("L'apport et les aides dépassent le prix : le prêt principal est négatif.");
 if(p.ptz_dur<=p.ptz_dif)alertes.push("La durée totale du PTZ doit dépasser le différé.");
 const n0=x=>Math.round(x).toLocaleString("en-US").replace(/,/g," ");
 if(princ>=0&&p.apport<fi.total)alertes.push(`L'apport ne couvre pas les frais (${n0(fi.total)} €) : ${n0(fi.total-p.apport)} € sont financés par le prêt, ce que beaucoup de banques refusent.`);
 if(p.ptz>Math.max(princ,0)+p.al)alertes.push("Le PTZ ne peut pas dépasser le montant des autres prêts.");
 if(p.date_liv<p.date_achat)alertes.push("La date de livraison est antérieure à la date d'achat.");
 const tx=taeg(rows,nb,total,fi.dossier+fi.garantie+fi.courtage);
 while(rows.length&&rows[rows.length-1].total<=0.005)rows.pop();
 const [y0,m0]=p.date_achat.split("-").map(Number),an={};
 rows.forEach(x=>{const y=y0+Math.floor((m0-1+x.m)/12);(an[y]=an[y]||[]).push(x)});
 const f=ph2[0]||{},dt=k=>rows.map(x=>+x[k].toFixed(2));
 return{kpis:{mf_princ:f.princ||0,mf_al:f.al||0,mf_ptz:f.ptz||0,mf_interet:f.interet||0,mf_ass:f.ass||0,
  mensualite_finale:ph2.length?ph2[0].total:0,max_avant:Math.max(0,...rows.filter(x=>x.phase===1).map(x=>x.total)),apres_ptz:apres,
  pret_principal:Math.max(princ,0),total_emprunte:total,interets,assurance,cout_credit:interets+assurance,total_rembourse:sum(rows,"total"),duree_mois:rows.length,mois_chantier:nb,
  frais_notaire:fi.notaire,frais_dossier:fi.dossier,frais_garantie:fi.garantie,courtage:fi.courtage,frais_initiaux:fi.total,
  cout_total:p.prix+fi.total+interets+assurance,taeg:tx,tf_pleine:p.tf+p.teom,mensualite_avec_tf:(ph2.length?ph2[0].total:0)+(p.tf+p.teom)/12},
  mensualites:dt("total"),capital:rows.map(x=>Math.round(x.bal)),
  detail:{princ:dt("princ"),al:dt("al"),ptz:dt("ptz"),interet:dt("interet"),ass:dt("ass")},
  periodes:periodes(rows,nb,p),
  annuel:Object.entries(an).map(([y,a])=>({annee:y,mois:a.length,mensualite_moy:sum(a,"total")/a.length,mini:Math.min(...a.map(x=>x.total)),maxi:Math.max(...a.map(x=>x.total)),interets:sum(a,"interet"),assurance:sum(a,"ass"),total:sum(a,"total"),taxe_fonciere:taxeFonciereAnnee(p,Number(y)),capital_fin:a[a.length-1].bal})),alertes};
}

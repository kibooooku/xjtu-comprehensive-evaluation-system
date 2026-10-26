package deyu.design;

import com.vp.plugin.*;
import com.vp.plugin.model.*;
import com.vp.plugin.model.factory.IModelElementFactory;
import com.vp.plugin.diagram.*;
import com.vp.plugin.diagram.shape.*;
import java.awt.Color;
import java.awt.Point;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import javax.xml.parsers.*;
import org.w3c.dom.*;

/** Local design-artifact generator. It never reads application credentials or calls cloud APIs. */
public class CurrentDesignPlugin implements VPPlugin, VPPluginCommandLineSupport {
  private IModelElementFactory factory;
  private DiagramManager dm;
  private ModelConvertionManager conversion;
  private Path output;
  private final Map<String,IModelElement> umlModels = new LinkedHashMap<>();
  private final Map<String,IDBTable> tables = new LinkedHashMap<>();
  private final Map<String,IDBColumn> columns = new LinkedHashMap<>();
  private final Map<String,IDBForeignKey> foreignKeys = new LinkedHashMap<>();
  private final java.util.List<String> exported = new ArrayList<>();

  public void loaded(VPPluginInfo info) {}
  public void unloaded() {}

  public void invoke(String[] args) {
    try {
      if (!Boolean.getBoolean("deyu.seed.copy.confirmed")) {
        throw new IllegalStateException("Run this design generator only on a copied seed project, with -Ddeyu.seed.copy.confirmed=true. Do not run it on an existing user project.");
      }
      String manifest = System.getProperty("deyu.manifest", args.length > 0 ? args[0] : "");
      if (manifest.isEmpty()) throw new IllegalArgumentException("Missing design manifest");
      DocumentBuilderFactory dbf = DocumentBuilderFactory.newInstance();
      dbf.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
      Document document = dbf.newDocumentBuilder().parse(new File(manifest));
      Element root = document.getDocumentElement();
      output = Paths.get(System.getProperty("deyu.output", root.getAttribute("output")));
      Files.createDirectories(output.resolve("图稿"));
      Files.createDirectories(output.resolve("VisualParadigm"));
      Files.createDirectories(output.resolve("源文件"));
      Files.deleteIfExists(output.resolve("native-generation-error.txt"));
      ApplicationManager app = ApplicationManager.instance();
      factory = IModelElementFactory.instance();
      dm = app.getDiagramManager();
      conversion = app.getModelConvertionManager();
      // The command runs on a workspace copy of the built-in seed, never on a user's open project.
      // Clearing its model roots avoids the GUI-only new-project dialog in command-line mode.
      for (IModelElement existing : app.getProjectManager().getProject().toModelElementArray()) {
        try { existing.delete(); } catch (UnsupportedOperationException frameworkElement) {
          System.out.println("Preserved framework metadata: " + existing.getModelType());
        }
      }
      app.getProjectManager().getProject().setName(root.getAttribute("name"));
      prepareUmlClasses(root);
      int limit = Integer.getInteger("deyu.limit", 9999);
      int count = 0;
      for (Element d : children(root, "diagram")) {
        if (count++ >= limit) break;
        renderDiagram(d);
      }
      if (limit > 1) {
        Element schema = children(root, "schema").get(0);
        createSchema(schema);
        for (Element v : children(schema, "view")) renderERD(schema, v);
      }
      File project = output.resolve("VisualParadigm/德育分统计系统-设计评审.vpp").toFile();
      if (!app.getProjectManager().saveProjectAs(project)) throw new IOException("Project save failed");
      // Native .vpp is the editable deliverable; VP's XML-export dialog is not headless-safe.
      Files.write(output.resolve("源文件/native-generation-report.txt"), exported, StandardCharsets.UTF_8);
      System.out.println("DESIGN_GENERATION_OK diagrams=" + exported.size() + " tables=" + tables.size());
    } catch (Throwable failure) {
      failure.printStackTrace();
      try { if (output != null) Files.writeString(output.resolve("native-generation-error.txt"), failure.toString(), StandardCharsets.UTF_8); } catch (Exception ignored) {}
      throw new RuntimeException(failure);
    }
  }

  private static java.util.List<Element> children(Element parent, String tag) {
    java.util.List<Element> result = new ArrayList<>();
    for (Node n = parent.getFirstChild(); n != null; n = n.getNextSibling()) {
      if (n instanceof Element && ((Element)n).getTagName().equals(tag)) result.add((Element)n);
    }
    return result;
  }

  private void style(IDiagramElement shape, Color fill, int fontSize) {
    shape.setForeground(new Color(42, 61, 79));
    shape.setBackground(fill);
    if(shape.getElementFont()!=null) {
      shape.getElementFont().setName("Microsoft YaHei UI");
      shape.getElementFont().setSize(fontSize);
    }
  }

  private void prepareUmlClasses(Element root) {
    Map<String,java.util.List<Element>> definitions=new LinkedHashMap<>();
    for(Element d:children(root,"diagram")) {
      if(!d.getAttribute("kind").equals("class")) throw new IllegalArgumentException("Only UML class and ER diagrams belong to the current deliverable");
      for(Element n:children(d,"node")) definitions.computeIfAbsent(n.getAttribute("id"),k->new ArrayList<>()).add(n);
    }
    for(Map.Entry<String,java.util.List<Element>> entry:definitions.entrySet()) {
      java.util.List<Element> variants=entry.getValue();
      variants.sort((a,b)->Integer.compare(children(b,"attribute").size()+children(b,"operation").size(),children(a,"attribute").size()+children(a,"operation").size()));
      Element preferred=variants.get(0);
      IClass clazz=factory.createClass(); clazz.setName(preferred.getAttribute("name"));
      if(!preferred.getAttribute("stereotype").isEmpty())clazz.addStereotype(preferred.getAttribute("stereotype"));
      Set<String> attributes=new LinkedHashSet<>(),operations=new LinkedHashSet<>();
      for(Element variant:variants) {
        for(Element a:children(variant,"attribute")) {
          String text=a.getTextContent().trim();int colon=text.indexOf(':');
          String names=colon>=0?text.substring(0,colon).trim():text;
          String type=colon>=0?text.substring(colon+1).trim():"String";
          for(String name:names.split(" / ")) {
            String raw=name.trim().replaceFirst("^[+\\-#]","");boolean derived=raw.startsWith("/");
            String clean=derived?raw.substring(1):raw;
            if(!attributes.add(clean))continue;
            IAttribute attr=clazz.createAttribute();attr.setName(clean);attr.setType(type);attr.setDerived(derived);attr.setVisibility("private");
          }
        }
        for(Element o:children(variant,"operation")) {
          String text=o.getTextContent().trim();if(!operations.add(text))continue;
          int paren=text.indexOf('('),close=text.lastIndexOf(')');
          String name=(paren>=0?text.substring(0,paren):text).trim().replaceFirst("^[+\\-#]","");
          IOperation op=clazz.createOperation();op.setName(name);op.setVisibility("public");
          int colon=close>=0?text.indexOf(':',close):text.indexOf(':');
          if(colon>=0)op.setReturnType(text.substring(colon+1).trim());
          if(paren>=0&&close>paren+1)for(String part:text.substring(paren+1,close).split(",")) {
            String[] parameter=part.trim().split(":",2);IParameter p=op.createParameter();p.setName(parameter[0].trim());
            p.setType(parameter.length>1?parameter[1].trim():"String");
          }
        }
      }
      umlModels.put(entry.getKey(),clazz);
    }
  }

  private void renderDiagram(Element source) throws Exception {
    if(!source.getAttribute("kind").equals("class"))throw new IllegalArgumentException("DFD is not part of the current diagrams");
    IDiagramUIModel diagram=dm.createDiagram("ClassDiagram");
    diagram.setName(source.getAttribute("id")+" "+source.getAttribute("name"));
    Map<String,IShapeUIModel> shapes=new LinkedHashMap<>();int index=0;
    for(Element node:children(source,"node")) {
      String id=node.getAttribute("id");IModelElement model=umlModels.get(id);
      if(model==null)throw new IllegalArgumentException("Unknown class "+id);
      IShapeUIModel shape=(IShapeUIModel)dm.createDiagramElement(diagram,model);
      if(shape instanceof IClassUIModel)((IClassUIModel)shape).setDisplayAsRobustnessAnalysisIcon(false);
      shape.setBounds(60+(index%4)*420,80+(index/4)*310,340,230);
      if(sourceHasPosition(node))shape.setBounds(number(node,"x"),number(node,"y"),number(node,"w"),number(node,"h"));
      shape.setDisplayStereotypeIcon(false);shape.setOverrideAppearanceWithStereotypeIcon(false);
      Color fill=node.getAttribute("stereotype").equals("service")?new Color(236,230,249):new Color(230,242,249);
      style(shape,fill,13);shape.resetCaption();
      if(shape instanceof IClassUIModel)((IClassUIModel)shape).setShowOperationSignature(true);
      shapes.put(id,shape);index++;
    }
    for(Element edge:children(source,"edge")) {
      String from=edge.getAttribute("from"),to=edge.getAttribute("to"),type=edge.getAttribute("type");
      if(!shapes.containsKey(from)||!shapes.containsKey(to))throw new IllegalArgumentException("Unknown UML endpoint "+from+" -> "+to);
      IRelationship relation;
      if(type.equals("dependency")) {
        IDependency d=factory.createDependency();d.setFrom(umlModels.get(from));d.setTo(umlModels.get(to));relation=d;
      } else if(type.equals("generalization")) {
        IGeneralization g=factory.createGeneralization();g.setFrom(umlModels.get(from));g.setTo(umlModels.get(to));relation=g;
      } else {
        IAssociation a=factory.createAssociation();a.setFrom(umlModels.get(from));a.setTo(umlModels.get(to));
        IAssociationEnd first=(IAssociationEnd)a.getFromEnd(),last=(IAssociationEnd)a.getToEnd();
        if(!edge.getAttribute("fromMultiplicity").isEmpty())first.setMultiplicity(edge.getAttribute("fromMultiplicity"));
        if(!edge.getAttribute("toMultiplicity").isEmpty())last.setMultiplicity(edge.getAttribute("toMultiplicity"));
        if(type.equals("composition"))first.setAggregationKind(IAssociationEnd.AGGREGATION_KIND_COMPOSITED);
        else if(type.equals("aggregation"))first.setAggregationKind(IAssociationEnd.AGGREGATION_KIND_AGGREGATION);
        relation=a;
      }
      relation.setName(edge.hasAttribute("displayLabel")?edge.getAttribute("displayLabel"):edge.getAttribute("label"));
      IDiagramElement connector=dm.createConnector(diagram,relation,shapes.get(from),shapes.get(to),points(edge));
      style(connector,Color.WHITE,11);connector.resetCaption();
    }
    if(!children(source,"node").isEmpty()&&!sourceHasPosition(children(source,"node").get(0))) {
      dm.layout(diagram,DiagramManager.LAYOUT_HIERARCHIC);dm.layout(diagram,DiagramManager.LAYOUT_ROUTE_CONNECTORS_ORTHOGONAL);
    }
    addLegend(diagram,source.getAttribute("name"),children(source,"note"));
    export(diagram,source.getAttribute("id"));
  }

  private static Color groupColor(String group) {
    if (group.equals("rbac")) return new Color(227,239,250);
    if (group.equals("evidence")) return new Color(224,243,232);
    if (group.equals("workflow")) return new Color(251,237,218);
    return new Color(239,229,248);
  }

  private static boolean sourceHasPosition(Element e) { return e.hasAttribute("x"); }
  private static int number(Element e,String name) { return Integer.parseInt(e.getAttribute(name)); }
  private static Point[] points(Element e) {
    java.util.List<Element> list=children(e,"point"); Point[] result=new Point[list.size()];
    for(int i=0;i<list.size();i++)result[i]=new Point(number(list.get(i),"x"),number(list.get(i),"y"));
    return result;
  }

  private void applyType(IDBColumn column, Element field) {
    String text = field.getAttribute("type").toUpperCase(Locale.ROOT);
    if (text.startsWith("DECIMAL") || text.startsWith("NUMERIC")) {
      java.util.regex.Matcher precision=java.util.regex.Pattern.compile(".*?\\((\\d+)\\s*,\\s*(\\d+)\\).*?").matcher(text);
      if(!precision.matches())throw new IllegalArgumentException("Missing decimal precision: "+text);
      column.setType("decimal",Integer.parseInt(precision.group(1)),Integer.parseInt(precision.group(2)));
    }
    else if (text.contains("INT")) column.setType("integer",10,0);
    else if (text.equals("BOOLEAN")) column.setType("boolean",1,0);
    else if (text.equals("TIMESTAMP") || text.equals("DATETIME")) column.setType("timestamp",0,0);
    else if (text.equals("DATE")) column.setType("date",0,0);
    else if (text.equals("UUID")) column.setType("varchar",36,0);
    else if (text.startsWith("VARCHAR")) {
      int length = 160;
      try { length = Integer.parseInt(text.replaceAll("[^0-9]", "")); } catch (Exception ignored) {}
      column.setType("varchar",length,0);
    } else column.setType("text",0,0);
    column.setPrimaryKey(Boolean.parseBoolean(field.getAttribute("pk")));
    column.setNullable(Boolean.parseBoolean(field.getAttribute("nullable")));
    column.setDescription(field.getAttribute("note") + " | logical type: " + field.getAttribute("type"));
  }

  private void createSchema(Element schema) {
    for (Element t : children(schema, "table")) {
      IDBTable table = factory.createDBTable(); table.setName(t.getAttribute("id")); table.setNickname(t.getAttribute("name"));
      StringBuilder description = new StringBuilder(t.getAttribute("name"));
      for (Element c : children(t, "constraint")) description.append("\n").append(c.getTextContent());
      table.setDescription(description.toString()); tables.put(t.getAttribute("id"),table);
      for (Element field : children(t,"field")) {
        if (!field.getAttribute("fk").isEmpty()) continue;
        IDBColumn col = table.createDBColumn(); col.setName(field.getAttribute("name")); applyType(col,field); columns.put(t.getAttribute("id")+"."+field.getAttribute("name"),col);
      }
    }
    for (Element t : children(schema,"table")) {
      IDBTable child = tables.get(t.getAttribute("id"));
      for (Element field : children(t,"field")) {
        if (field.getAttribute("fk").isEmpty()) continue;
        String target = field.getAttribute("fk"); int dot = target.lastIndexOf('.');
        String parentName = target.substring(0,dot); IDBColumn parentColumn = columns.get(target);
        if (parentColumn == null) throw new IllegalArgumentException("Missing referenced column: " + target);
        IDBForeignKey fk = factory.createDBForeignKey(); fk.setName("fk_"+t.getAttribute("id")+"_"+field.getAttribute("name")); fk.setFrom(tables.get(parentName)); fk.setTo(child);
        fk.setFromMultiplicity(Boolean.parseBoolean(field.getAttribute("nullable"))?"0..1":"1"); fk.setToMultiplicity("0..*");
        IDBForeignKeyConstraint constraint = factory.createDBForeignKeyConstraint(); constraint.setRefColumn(parentColumn); constraint.setForeignKey(fk);
        IDBColumn generated = null;
        java.util.Iterator<?> iterator = fk.foreignKeyColumnsIterator();
        while (iterator.hasNext()) { Object obj = iterator.next(); if (obj instanceof IDBColumn) { generated=(IDBColumn)obj; break; } }
        if (generated == null) { generated = child.createDBColumn(); }
        generated.setName(field.getAttribute("name")); applyType(generated,field);
        columns.put(t.getAttribute("id")+"."+field.getAttribute("name"),generated);
        foreignKeys.put(t.getAttribute("id")+"."+field.getAttribute("name"),fk);
      }
    }
    for(Element t:children(schema,"table"))for(Element source:children(t,"index")) {
      IDBIndex index=tables.get(t.getAttribute("id")).createDBIndex();index.setName(source.getAttribute("name"));index.setUnique(Boolean.parseBoolean(source.getAttribute("unique")));
      for(Element ref:children(source,"column")) {
        IDBColumn column=columns.get(t.getAttribute("id")+"."+ref.getAttribute("name"));
        if(column==null)throw new IllegalArgumentException("Unknown index column "+ref.getAttribute("name"));index.addColumn(column);
      }
    }
    java.util.List<String> audit=new ArrayList<>();
    for(Element t:children(schema,"table")){
      String id=t.getAttribute("id");IDBColumn[] actual=tables.get(id).toDBColumnArray();Set<String> seen=new HashSet<>();boolean duplicates=false;
      for(IDBColumn c:actual)if(!seen.add(c.getName()))duplicates=true;
      Set<String> expectedNames=new HashSet<>();for(Element f:children(t,"field"))expectedNames.add(f.getAttribute("name"));
      int expected=children(t,"field").size();boolean namesMatch=seen.equals(expectedNames);audit.add(id+" expected="+expected+" actual="+actual.length+" duplicateNames="+duplicates+" nameSetMatches="+namesMatch);
      if(actual.length!=expected||duplicates||!namesMatch)throw new IllegalStateException("Native ER column mismatch: "+id+" expected="+expected+" actual="+actual.length+" nameSetMatches="+namesMatch);
    }
    int fieldCount=0,uniqueCount=0;
    for(IDBTable table:tables.values()) {fieldCount+=table.toDBColumnArray().length;for(IDBIndex index:table.toDBIndexArray())if(index.isUnique())uniqueCount++;}
    audit.add("TOTAL tables="+tables.size()+" fields="+fieldCount+" foreignKeys="+foreignKeys.size()+" uniqueIndexes="+uniqueCount);
    try{Files.write(output.resolve("源文件/native-schema-audit.txt"),audit,StandardCharsets.UTF_8);}catch(IOException e){throw new RuntimeException(e);}
  }

  private void renderERD(Element schema, Element view) throws Exception {
    String group = view.getAttribute("group"); boolean overview=group.equals("all");
    Set<String> core=new LinkedHashSet<>();
    for(Element ref:children(view,"core"))core.add(ref.getAttribute("id"));
    if(core.isEmpty())for(Element t:children(schema,"table"))if(overview||t.getAttribute("group").equals(group))core.add(t.getAttribute("id"));
    java.util.List<Element> allRelations=children(schema,"relation"),selectedRelations=new ArrayList<>();
    for(Element ref:children(view,"relation"))selectedRelations.add(allRelations.get(number(ref,"index")));
    if(selectedRelations.isEmpty())for(Element r:allRelations)if(overview||core.contains(r.getAttribute("to")))selectedRelations.add(r);
    Set<String> shown=new LinkedHashSet<>(core);
    for(Element r:selectedRelations){shown.add(r.getAttribute("from"));shown.add(r.getAttribute("to"));}
    for(Element position:children(view,"position"))shown.add(position.getAttribute("id"));
    IDiagramUIModel diagram = dm.createDiagram("ERDiagram"); diagram.setName(view.getAttribute("id")+" "+view.getAttribute("name"));
    Map<String,IShapeUIModel> shapes = new LinkedHashMap<>(); int index=0;
    for (Element t : children(schema,"table")) {
      String id=t.getAttribute("id"); if (!shown.contains(id)) continue;
      IDBTableUIModel shape=(IDBTableUIModel)dm.createDiagramElement(diagram,tables.get(id));
      shape.setBounds(60+(index%4)*470,80+(index/4)*380,370,overview?80:300);
      for(Element p:children(view,"position"))if(p.getAttribute("id").equals(id))shape.setBounds(number(p,"x"),number(p,"y"),number(p,"w"),number(p,"h"));
      style(shape,core.contains(id)?groupColor(t.getAttribute("group")):new Color(245,246,248),overview?14:12);
      shape.setShowColumnsMode(overview?IDBTableUIModel.SHOW_COLUMNS_MODE_HIDE_ALL:core.contains(id)?IDBTableUIModel.SHOW_COLUMNS_MODE_SHOW_ALL:IDBTableUIModel.SHOW_COLUMNS_MODE_PRIMARY_KEYS_ONLY);
      shape.resetCaption(); shapes.put(id,shape); index++;
    }
    int edgeIndex=0;
    for (Element r : selectedRelations) {
      String from=r.getAttribute("from"),to=r.getAttribute("to");
      if (!shapes.containsKey(from)||!shapes.containsKey(to)) continue;
      IDBForeignKey fk=foreignKeys.get(to+"."+r.getAttribute("toField")); if(fk==null) continue;
      String cardinality=r.getAttribute("cardinality");
      String right=cardinality.contains(":")?cardinality.substring(cardinality.indexOf(':')+1):cardinality;
      fk.setToMultiplicity(r.hasAttribute("toMultiplicity")?r.getAttribute("toMultiplicity"):right.equals("0..1")?"0..1":right.equals("1")?"1":"0..*");
      Point[] route=new Point[0];for(Element p:children(view,"edgePosition"))if(p.getAttribute("id").equals("e"+edgeIndex))route=points(p);
      IDiagramElement connector=dm.createConnector(diagram,fk,shapes.get(from),shapes.get(to),route); style(connector,Color.WHITE,11); connector.resetCaption(); edgeIndex++;
    }
    if(children(view,"position").isEmpty()) {dm.layout(diagram,DiagramManager.LAYOUT_HIERARCHIC); dm.layout(diagram,DiagramManager.LAYOUT_ROUTE_CONNECTORS_ORTHOGONAL);}
    addLegend(diagram,view.getAttribute("name"),Collections.emptyList());
    export(diagram,view.getAttribute("id"));
  }

  private void addLegend(IDiagramUIModel diagram,String title,java.util.List<Element> notes) {
    try {
      int minX=0,minY=0,maxY=0;
      for(IDiagramElement element:diagram.toDiagramElementArray()) { minX=Math.min(minX,element.getX()); minY=Math.min(minY,element.getY()); maxY=Math.max(maxY,element.getY()+element.getHeight()); }
      StringBuilder caption=new StringBuilder(title).append("\n当前实现版 · 2026-10-26 · UML / ER");
      int used=0; for(Element n:notes) { if(used++<3) caption.append("\n").append(n.getTextContent()); }
      INOTE note=factory.createNOTE();note.setName(caption.toString());note.setDescription(caption.toString());
      IShapeUIModel text=(IShapeUIModel)dm.createDiagramElement(diagram,note);
      text.setCustomText(caption.toString()); text.setBounds(minX+20,maxY+45,1200,100+Math.min(3,notes.size())*28); style(text,new Color(250,250,244),14); text.resetCaption();
    } catch(Throwable ignored) { System.out.println("Legend decoration skipped: "+ignored.getClass().getSimpleName()); }
  }

  private void export(IDiagramUIModel diagram,String id) {
    dm.openDiagram(diagram);
    output.resolve("VisualParadigm/原生导出").toFile().mkdirs();
    ExportDiagramAsImageOption png=new ExportDiagramAsImageOption(2); png.setScale(1.3f); png.setGraphicAntiAliasing(true); png.setTextAntiAliasing(true);
    conversion.exportDiagramAsImage(diagram,output.resolve("VisualParadigm/原生导出/"+id+".png").toFile(),png);
    ExportDiagramAsImageOption svg=new ExportDiagramAsImageOption(4); svg.setScale(1.0f); svg.setTextAntiAliasing(true);
    conversion.exportDiagramAsImage(diagram,output.resolve("VisualParadigm/原生导出/"+id+".svg").toFile(),svg);
    exported.add(id+" | "+diagram.getName()+" | elements="+diagram.toDiagramElementArray().length);
    System.out.println("EXPORTED "+id);
  }
}

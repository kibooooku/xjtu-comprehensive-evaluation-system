import java.lang.reflect.*;
import java.util.*;

/** Visual Paradigm API inspection helper for design artifacts only. */
public class ApiInspect {
  public static void main(String[] args) throws Exception {
    for (String n : args) {
      Class<?> c = Class.forName(n, false, ApiInspect.class.getClassLoader());
      System.out.println("=== " + n);
      String filter = System.getProperty("inspect.filter", ".*");
      for (Method m : c.getMethods()) {
        if (m.getDeclaringClass() != Object.class && m.getName().matches(filter)) System.out.println(m.toGenericString());
      }
      for (Field f : c.getFields()) {
        if (Modifier.isStatic(f.getModifiers()) && f.getName().matches(filter) && (f.getType() == String.class || f.getType() == int.class)) {
          try { System.out.println(f.getName() + "=" + f.get(null)); } catch (Throwable t) {}
        }
      }
    }
  }
}

# Kotlinx Serialization 会通过反射访问生成的 serializer 字段。
-keepattributes *Annotation*, InnerClasses
-keepclassmembers class kotlinx.serialization.json.** { *** Companion; }
-keep,includedescriptorclasses class com.keji.classroomnote.**$$serializer { *; }
-keepclassmembers class com.keji.classroomnote.** { *** Companion; }


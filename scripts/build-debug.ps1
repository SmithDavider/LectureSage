param(
    [Parameter(Mandatory = $true)]
    [string] $GradleHome,

    [string] $JavaHome,

    [string] $AndroidHome
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($JavaHome)) {
    throw "请传入 Android Studio 自带 JDK 17 的路径，例如 .../jbr"
}

if ([string]::IsNullOrWhiteSpace($AndroidHome)) {
    throw "请传入 Android SDK 路径，例如 C:\Users\<用户名>\AppData\Local\Android\Sdk"
}

$gradleBat = Join-Path $GradleHome "bin\gradle.bat"
if (-not (Test-Path $gradleBat)) {
    throw "未找到 $gradleBat，请确认传入的是 Gradle 根目录"
}

$env:JAVA_HOME = $JavaHome
$env:ANDROID_HOME = $AndroidHome
$env:GRADLE_USER_HOME = Join-Path $PSScriptRoot "..\.gradle-home"

& $gradleBat ":app:assembleDebug"
